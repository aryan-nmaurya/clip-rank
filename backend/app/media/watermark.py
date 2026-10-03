"""Optional user branding, rendered as pixels rather than an FFmpeg expression."""
import unicodedata
from PIL import Image,ImageDraw
from app.media.captions import font


class Watermark:
    OPACITY=.5

    @staticmethod
    def validate(enabled,text):
        if type(enabled) not in (bool,int) or enabled not in (True,False,0,1):
            raise ValueError('Watermark enabled must be true or false.')
        if not isinstance(text,str) or len(text)>60 or any(unicodedata.category(c).startswith('C') for c in text):
            raise ValueError('Use a single line of watermark text, up to 60 characters.')
        text=text.strip()
        if enabled and not text:raise ValueError('Enter watermark text before enabling it.')
        return bool(enabled),text

    @classmethod
    def render(cls,output,width,height,text):
        _,text=cls.validate(True,text)
        image=Image.new('RGBA',(width,height),(0,0,0,0))
        draw=ImageDraw.Draw(image)
        size=max(12,round(width*.032))
        face=font(size)
        while size>8 and draw.textlength(text,font=face)>width*.5:
            size-=1;face=font(size)
        if draw.textlength(text,font=face)>width*.5:
            raise ValueError('Watermark is too wide; use shorter text.')
        # Below hook/rank graphics, above speech captions, inside mobile margins.
        alpha=round(255*cls.OPACITY)
        draw.text((round(width*.08),round(height*.22)),text,font=face,anchor='lt',
                  fill=(255,255,255,alpha),stroke_width=max(1,round(width/700)),
                  stroke_fill=(0,0,0,alpha))
        output.parent.mkdir(parents=True,exist_ok=True)
        image.save(output)
        return output
