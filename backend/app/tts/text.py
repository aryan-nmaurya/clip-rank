"""Pronounce written English numbers without changing their values."""
import re


def spoken_numbers(text):
    from num2words import num2words
    def replace(match):
        raw=match.group(0).replace(',','')
        whole,dot,fraction=raw.partition('.')
        value=num2words(int(whole),lang='en')
        if dot: value+=' point '+' '.join(num2words(int(digit),lang='en') for digit in fraction)
        return value
    return re.sub(r'(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?!\w)',replace,text).replace('%',' percent')


def equivalent_spoken_text(actual,expected):
    # Numeric typography can differ in ASR ("three" versus "3"). Missing,
    # invented, repeated or different-valued speech still fails comparison.
    normalize=lambda text:spoken_tokens(text)
    return normalize(actual)==normalize(expected)


def spoken_tokens(text):
    text=re.sub(r'(\d)\s+(\.\d)',r'\1\2',text)
    return re.findall(r'\w+',spoken_numbers(text).casefold())


def speech_consistent(actual,expected):
    a,b=spoken_tokens(actual),spoken_tokens(expected)
    if not a or not b: return False
    number_words=set('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion point percent'.split())
    if [w for w in a if w in number_words]!=[w for w in b if w in number_words]: return False
    negation=lambda text:re.findall(r"\b(?:not|never|no|cannot|\w+n't)\b",text.casefold())
    if negation(actual)!=negation(expected): return False
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        next_row=[i]
        for j,y in enumerate(b,1):next_row.append(min(next_row[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=next_row
    return row[-1]<=2 and row[-1]/max(len(a),len(b))<=.12
