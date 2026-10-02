"""Original mobile-safe diagrams, never stock imagery passed off as product footage."""
import math
from PIL import Image, ImageDraw
from app.media.captions import font, wrap, CaptionRenderer
from app.core.runtime import run_process
from app.media.reframer import VideoReframer


class GraphicsRenderer:
    @staticmethod
    def draw(beat,output,index,count,publisher):
        image=Image.new('RGB',(1080,1920),'#101425')
        draw=ImageDraw.Draw(image)
        for y in range(1920):
            shade=round(18+16*y/1920)
            draw.line((0,y,1080,y),fill=(shade,shade+5,shade+18))
        for x in range(90,1050,100):
            for y in range(100,1840,100): draw.ellipse((x,y,x+3,y+3),fill='#363f57')
        draw.rounded_rectangle((85,130,500,192),radius=24,fill='#253952')
        draw.text((110,142),'AI & FUTURE TECH',font=font(30),fill='#82dff6')
        headline=beat['headline'].upper()
        title_font=font(70)
        lines=wrap(draw,headline,title_font,860)
        if len(lines)>3: raise ValueError('Graphic headline exceeds the safe area.')
        for i,line in enumerate(lines):
            draw.text((85,250+i*86),line,font=title_font,fill='white')
        draw.text((85,560),'EXPLANATORY DIAGRAM',font=font(25),fill='#a7afc8')
        if beat.get('rank'):
            draw.rounded_rectangle((825,125,960,205),radius=20,fill='#81dff1')
            draw.text((892,165),f'#{beat["rank"]}',font=font(45),anchor='mm',fill='#101425')
        nodes=beat['diagram']['nodes']
        for i,node in enumerate(nodes):
            y=675+i*155
            draw.rounded_rectangle((85,y,945,y+113),radius=28,fill='#25324c',outline='#557591',width=2)
            draw.ellipse((111,y+32,160,y+81),fill='#81dff1')
            draw.text((135,y+56),str(i+1),font=font(27),anchor='mm',fill='#102036')
            face=font(43)
            while draw.textlength(node,font=face)>700 and face.size>28: face=font(face.size-2)
            if draw.textlength(node,font=face)>700: raise ValueError('Diagram label cannot fit.')
            draw.text((190,y+56),node,font=face,anchor='lm',fill='white')
            if i<len(nodes)-1:
                draw.line((515,y+113,515,y+147),fill='#81dff1',width=4)
                draw.polygon([(506,y+136),(524,y+136),(515,y+147)],fill='#81dff1')
        draw.text((85,1675),'RESEARCH: '+publisher[:42].upper(),font=font(22),fill='#a7afc8')
        for i in range(count):
            x=85+i*(860/count)
            draw.rounded_rectangle((x,1735,x+860/count-12,1742),radius=3,fill='#81dff1' if i<=index else '#364058')
        image.save(output)
        return output

    @staticmethod
    def render(beat,speech,folder,index,count,publisher):
        duration=speech['duration']+.25
        image=GraphicsRenderer.draw(beat,folder/f'graphic_{index}.png',index,count,publisher)
        motion=folder/f'motion_{index}.mp4'
        # A restrained drift over the complete graphic; all critical elements have wide margins.
        frames=math.ceil(duration*30)
        run_process(['ffmpeg','-v','error','-y','-loop','1','-i',str(image),'-vf',
                     f"zoompan=z='1.02+0.008*sin(on/45)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s=1080x1920:fps=30",
                     '-t',str(duration),'-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',str(motion)])
        captions=CaptionRenderer.write_ass(folder/f'captions_{index}.ass',speech['segments'],0,duration)
        return VideoReframer.reframe_to_vertical(motion,folder/f'segment_{index}.mp4',duration=duration,
                    captions_ass=captions,narration_wav=folder/f'voice_{index}.wav'),duration
