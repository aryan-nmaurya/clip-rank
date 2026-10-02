from urllib.parse import urlparse


class RightsPolicyEngine:
    @staticmethod
    def evaluate(assets,policy='documented_permission'):
        if not assets:
            return {'passed':False,'reason':'No visual provenance.'}
        reasons=[]
        for asset in assets:
            required=('source_url','source_type','creator','license','rights_status','fetched_at')
            if any(not asset.get(k) for k in required):
                reasons.append('Asset provenance is incomplete.'); continue
            kind=asset['source_type']; license=asset['license'].lower()
            if kind=='cliprank_generated' and asset['creator']=='ClipRank' and license=='original': continue
            if policy=='user_managed' and asset['rights_status']=='user_managed' and license=='unknown': continue
            if asset['rights_status']!='commercial_use_permitted' or not asset.get('license_evidence_url'):
                reasons.append('Commercial reuse rights are not documented.'); continue
            if any(term in license for term in ('noncommercial','non-commercial','cc-by-nc','cc-by-nd','unknown')):
                reasons.append('The license does not permit this commercial edited use.'); continue
            if asset.get('attribution_required') and not asset.get('attribution'):
                reasons.append('Required attribution is missing.')
        managed=any(a.get('rights_status')=='user_managed' for a in assets)
        return {'passed':not reasons,'policy':policy,'rights_verified':not managed and not reasons,
                'reason':('Reuse rights are managed by the channel owner; provenance retained.' if managed else 'All assets have explicit production rights.') if not reasons else ' '.join(reasons)}


class OriginalityEngine:
    @staticmethod
    def evaluate_visual(records):
        checks=[]
        for record in records:
            plan=record.get('script') or record.get('plan') or {}
            timeline=record.get('timeline',[])
            narrated=[beat for beat in timeline if beat.get('speech') and beat['speech'].get('words')]
            # Require original narration and a distinct hook/story or ranking,
            # plus actual edited/captioned footage verified by final review.
            original_text=' '.join(beat.get('narration','') for beat in narrated)
            checks.append(bool(narrated and len(original_text.split())>=5 and (plan.get('hook') or record.get('hook'))
                and record.get('qc',{}).get('captions_checked') and record.get('final_review',{}).get('passed')))
        return {'passed':bool(checks) and all(checks),'reason':'Original grounded narration, hook, edited footage, synchronized captions and final visual review.' if checks and all(checks) else 'Missing original commentary or reviewed production effort.'}
    @staticmethod
    def evaluate(script,assets):
        beats=script.get('beats',[])
        roles={beat.get('role') for beat in beats}
        contributions=script.get('original_contributions',[])
        passed=(len(beats)>=4 and {'hook','explanation','payoff'}.issubset(roles)
                and len(contributions)>=2 and all(a.get('source_type')=='cliprank_generated' for a in assets)
                and any(b.get('diagram') for b in beats)
                and len(' '.join(b.get('narration','') for b in beats).split())>=60)
        return {'passed':passed,'reason':'Original sourced script, explanation and custom diagrams.' if passed else 'Insufficient original editorial contribution.'}


class QualityGate:
    REQUIRED=('technical','rights','originality','factuality','captions','audio','visual')

    @classmethod
    def evaluate(cls,checks,scores,threshold=80):
        failed=[key for key in cls.REQUIRED if checks.get(key,{}).get('passed') is not True]
        for key in ('hook','clarity','pacing','original_contribution','audience_fit'):
            score=scores.get(key)
            if type(score) not in (int,float) or not threshold<=score<=100:
                failed.append(key)
        return {'passed':not failed,'failed':failed,'score':min(scores.values()) if scores and not failed else 0,
                'score_basis':'Internal editorial heuristics, not viral probabilities.'}


class MetadataAgent:
    @staticmethod
    def generate(script,research,profile):
        title=script['title'][:100]
        description=script.get('summary','')[:700]+'\n\nResearch source: '+research['source_url']
        text=(' '.join(beat['narration'] for beat in script['beats'])).lower()
        for affiliate in profile.affiliates:
            if affiliate.product.lower() in text and urlparse(affiliate.url).scheme=='https':
                description+='\n\n'+affiliate.disclosure+'\n'+affiliate.product+': '+affiliate.url
        return {'title':title,'description':description+'\n\n#Technology #Shorts',
                'category_id':'28','privacy':profile.privacy,'made_for_kids':profile.made_for_kids}
