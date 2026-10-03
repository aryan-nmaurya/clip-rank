"""Inspect the encoded deliverable, then expose it only after objective and model QC."""
import json
import math
import re
from pathlib import Path
import numpy as np
from app.core.runtime import run_process
from app.media.ffmpeg_core import FFmpegCore
from app.media.moments import MomentAnalyzer


class ProductionQC:
    MIN_DURATION=10.1

    @classmethod
    def require_duration(cls,duration):
        if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<cls.MIN_DURATION:
            raise ValueError('Every finished Short must be longer than 10 seconds. Select a longer complete moment; looping or frozen padding is not allowed.')
        return True

    @staticmethod
    def validate_words(words, duration, expected_text=None):
        previous = -1.
        for word in words:
            start, end = word['start'], word['end']
            if not all(type(t) in (int,float) and math.isfinite(t) for t in (start,end)):
                raise ValueError('Caption times are not finite numbers.')
            if not 0 <= start < end <= duration + .03 or start < previous - .03 or not word['word'].strip():
                raise ValueError('Caption words are missing, duplicated, overlapping or outside speech bounds.')
            previous = end
        if expected_text is not None:
            normalize = lambda text: re.findall(r'\w+', text.lower())
            if not words or normalize(' '.join(w['word'] for w in words)) != normalize(expected_text):
                raise ValueError('Caption words do not exactly match the narration.')
        return True

    @staticmethod
    def master_audio(video, output, settings=None):
        measurement = run_process(['ffmpeg','-v','info','-i',str(video),'-vn','-af',
            'loudnorm=I=-14:TP=-2:LRA=8:print_format=json','-f','null','-'], include_stderr=True).decode(errors='replace')
        matches = re.findall(r'\{\s*"input_i".*?\}', measurement, re.S)
        if not matches:
            raise ValueError('Cannot measure final audio loudness.')
        data = json.loads(matches[-1])
        numbers = [float(data[k]) for k in ('input_i','input_tp','input_lra','input_thresh','target_offset')]
        if not all(math.isfinite(n) for n in numbers):
            raise ValueError('The finished video has no usable audio. Silent production outputs are not published.')
        normalize = ('loudnorm=I=-14:TP=-2:LRA=8:linear=true:'
            f'measured_I={numbers[0]}:measured_TP={numbers[1]}:measured_LRA={numbers[2]}:'
            f'measured_thresh={numbers[3]}:offset={numbers[4]},aresample=48000,'
            'alimiter=limit=0.8:level=false:latency=true')
        cmd=['ffmpeg','-v','error','-y','-i',str(video)]
        if (settings or {}).get('watermark_enabled'):
            from app.media.watermark import Watermark
            info=FFmpegCore.get_video_info(video)
            graphic=Watermark.render(output.parent/'watermark.png',info['width'],info['height'],settings.get('watermark_text',''))
            cmd+=['-i',str(graphic),'-filter_complex','[0:v][1:v]overlay=0:0:eof_action=repeat[outv]',
                  '-map','[outv]','-map','0:a:0','-c:v','libx264','-crf','18','-preset','veryfast','-pix_fmt','yuv420p']
        else:cmd+=['-map','0:v:0','-map','0:a:0','-c:v','copy']
        run_process(cmd+['-af',normalize,'-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
                         '-movflags','+faststart',str(output)])
        return output

    @staticmethod
    def inspect(video, expected_duration, workspace, timeline, require_narration=True):
        info = FFmpegCore.validate_output(video, expected_duration)
        workspace.mkdir(parents=True, exist_ok=True)
        raw = run_process(['ffmpeg','-v','error','-i',str(video),'-an','-vf','fps=6,scale=180:320',
                           '-pix_fmt','gray','-f','rawvideo','-'])
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1,320,180)
        if len(frames) < 3:
            raise ValueError('Final video does not contain enough reviewable frames.')
        action = frames[:,40:275,12:168]
        black = (action.mean(axis=(1,2)) < 5) & (action.std(axis=(1,2)) < 3)
        if np.convolve(black.astype(int), np.ones(2,dtype=int), 'valid').max() >= 2:
            raise ValueError('An unexpected black section is present in the rendered action area.')
        same = np.mean(np.abs(np.diff(frames.astype(np.float32),axis=0)),axis=(1,2)) < .02
        if len(same) >= 10 and np.convolve(same.astype(int),np.ones(10,dtype=int),'valid').max() >= 10:
            raise ValueError('The final video contains an accidental frozen section longer than 1.5 seconds.')
        # Inspect native export-rate samples. Downsampling can overshoot and falsely
        # report clipped audio even when the delivered 48 kHz stream is intact.
        audio = run_process(['ffmpeg','-v','error','-i',str(video),'-vn','-ar','48000','-ac','2','-f','f32le','-'])
        # Keep both channels: FFmpeg's default stereo-to-mono downmix adds gain
        # to identical channels and is not a measurement of the published mix.
        samples = np.frombuffer(audio,dtype=np.float32).reshape(-1,2)
        if not len(samples) or not np.all(np.isfinite(samples)) or float(np.mean(samples**2)) < .00001:
            raise ValueError('Final audio is empty or inaudible.')
        peak = float(np.max(np.abs(samples)))
        if peak >= .999 or np.mean(np.abs(samples) > .98) > .0001:
            raise ValueError('The finished audio clips or distorts.')
        if require_narration:
            for item in timeline:
                speech = item.get('speech')
                if not speech:
                    raise ValueError('A ranking segment is missing narration and timed captions.')
                ProductionQC.validate_words(speech['words'], item['duration'], speech['text'])
                if speech['duration'] > item['duration'] - .1:
                    raise ValueError('Narration is rushed or cut off by the video ending.')
                lo, hi = int(item['timeline_start']*48000), int((item['timeline_start']+speech['duration'])*48000)
                if hi <= lo or float(np.mean(samples[lo:hi]**2)) < .00003:
                    raise ValueError('Narration is missing from the finished mix.')
        # Boundary frames are sampled on BOTH sides of every transition, plus speech and payoff.
        times = {0.04, min(1.0,info['duration']-.05), max(0,info['duration']-.1)}
        for item in timeline:
            start, duration = item['timeline_start'], item['duration']
            times.update((max(.01,start-.06),start+.06,start+duration*.35,start+duration*.7,start+duration-.06))
            if item.get('payoff_relative') is not None:
                times.add(start+item['payoff_relative'])
        sampled = []
        for idx,t in enumerate(sorted(times)):
            sampled.append({'start': max(0,min(t,info['duration']-.08)), 'end': max(.08,min(t+.08,info['duration']))})
        sheets = []
        for idx in range(0,len(sampled),8):
            sheets.append(MomentAnalyzer.contact_sheet(video,sampled[idx:idx+8],workspace/f'final_{idx}.jpg'))
        # The proxy is produced FROM the finished MP4 and keeps the exact finished audio.
        proxy = workspace / 'finished-review.mp4'
        run_process(['ffmpeg','-v','error','-y','-i',str(video),'-vf','scale=540:960','-c:v','libx264','-crf','29',
                     '-preset','veryfast','-maxrate','650k','-bufsize','1300k','-c:a','aac','-b:a','64k',
                     '-ar','48000','-movflags','+faststart',str(proxy)])
        return {'passed':True,'width':info['width'],'height':info['height'],'duration':info['duration'],
                'peak_amplitude':round(peak,4),'checked_frames':len(frames),'review_samples':len(times),
                'captions_checked':require_narration,'audio_master':'-14 LUFS target / -1.5 dBTP ceiling',
                'sheets':sheets,'proxy':proxy,'info':info}

    @staticmethod
    def rank_reveal(output, rank):
        # Original synthesized punctuation, no copyrighted samples or meme sound pack.
        output.parent.mkdir(parents=True,exist_ok=True)
        run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'sine=frequency={540+rank*60}:duration=0.12:sample_rate=48000',
                     '-af','afade=t=out:st=0.025:d=0.095','-c:a','pcm_s16le',str(output)])
        return output
