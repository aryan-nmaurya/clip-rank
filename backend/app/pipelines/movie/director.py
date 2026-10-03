"""Deterministic scores and editorial format decisions, never viral probabilities."""
from app.pipelines.movie.models import DIMENSIONS

class MovieMomentScorer:
    WEIGHTS={'hook_strength':.14,'visual_payoff':.13,'dialogue_strength':.03,'performance_strength':.04,
        'emotional_impact':.08,'surprise':.08,'standalone_clarity':.13,'rewatchability':.08,
        'caption_potential':.02,'commentary_potential':.02,'aesthetic_potential':.03,
        'music_fit':.02,'pacing':.07,'technical_quality':.08,'audience_fit':.05}
    @classmethod
    def score(cls,analysis):
        s=analysis['scores']
        return round(sum(s[k]*w for k,w in cls.WEIGHTS.items())-s['context_requirement']*.08,2)
    @classmethod
    def qualifies(cls,analysis):
        from app.core import qc
        if not qc.enabled():
            return True        # quality control off: every analysed moment may be produced
        s=analysis['scores']
        return (analysis['complete_moment'] is True and analysis['clean_source'] is True
            and analysis['contains_watermark'] is False and analysis['confidence']>=.85
            and min(s[k] for k in ('hook_strength','standalone_clarity','pacing','technical_quality','audience_fit'))>=70
            and max(s['visual_payoff'],s['dialogue_strength'],s['aesthetic_potential'])>=80
            and cls.score(analysis)>=72)

class MovieFormatDirector:
    @staticmethod
    def voice(mood,configured):
        if configured and not configured.startswith('pocket:'):return configured
        if mood in ('dominance','dark','epic'):return 'pocket:javert'
        if mood in ('action','funny'):return 'pocket:marius'
        return 'pocket:alba'
    @staticmethod
    def choose(analysis,has_dialogue,has_audio,music_available):
        s=analysis['scores']
        if (has_dialogue and has_audio and min(s[k] for k in ('dialogue_strength','dialogue_clarity',
                'dialogue_importance','performance_strength','audio_quality','standalone_clarity'))>=75
                and s['context_requirement']<=40):
            return {'format':'DIALOGUE','reason':'Original dialogue and performance carry this complete, understandable scene.'}
        if (s['commentary_potential']>=78 and s['interruption_cost']<65 and analysis['commentary']
                and len(analysis['commentary_value'].strip())>=25):
            return {'format':'COMMENTARY','reason':analysis['commentary_value']}
        if (music_available and min(s[k] for k in ('aesthetic_potential','visual_payoff','music_fit'))>=80
                and s['context_requirement']<=35 and s['dialogue_importance']<65):
            return {'format':'AESTHETIC','reason':'Visual composition and movement carry the moment with authorized, mood-matched music.'}
        return {'format':'REJECT','reason':('A cinematic moment needs a rights-documented, mood-matched music track.'
            if s['aesthetic_potential']>=80 and not music_available else 'No presentation format meets the movie quality gate.')}
