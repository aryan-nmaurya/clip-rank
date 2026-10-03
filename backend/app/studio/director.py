import json
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.core.database import get_connection
from app.studio import store
from app.studio.models import now,PILLARS


class PerformanceAnalyzer:
    @staticmethod
    def intelligence():
        with get_connection() as c:
            records=[json.loads(r['data']) for r in c.execute('SELECT data FROM performance').fetchall()]
        groups={}
        for record in records:
            # Missing outcomes remain missing; no invented zero views or retention.
            retention=record.get('averageViewPercentage')
            if type(retention) not in (int,float) or not math.isfinite(retention): continue
            category=record.get('category','unknown')
            groups.setdefault(category,[]).append(max(0,min(150,retention)))
        all_values=[v for values in groups.values() for v in values]
        baseline=sum(all_values)/len(all_values) if all_values else 50
        patterns={}
        for key,values in groups.items():
            # Five observations before any adjustment, with ten baseline pseudo-observations.
            smoothed=(sum(values)+10*baseline)/(len(values)+10)
            patterns[key]={'samples':len(values),'smoothed_retention':round(smoothed,2),
                          'priority_adjustment':max(-8,min(8,(smoothed-baseline)/4)) if len(values)>=5 else 0}
        recent=[store.opportunity(t['opportunity_id']) for t in store.tasks() if t['status']!='CANCELLED']
        return {'performance_patterns':patterns,'recent_topics':[r['topic'] for r in recent if r][:60],
                'content_pillar_distribution':{pillar:sum(1 for r in recent if r and r['category']==pillar) for pillar in store.profile().pillars},
                'sample_count':len(records),'learning_status':'Collecting sufficient samples' if len(records)<5 else 'Smoothed observations available',
                'revenue':None}


class ContentDirector:
    WEIGHTS={'hook_strength':.15,'visual_payoff':.19,'surprise':.12,'emotion':.10,'clarity':.12,
             'retention_potential':.10,'rewatchability':.07,'shareability':.05,'commentary_potential':.05,'audience_fit':.05}
    LEGACY_WEIGHTS={'freshness':.13,'audience_fit':.19,'novelty':.10,'visual_potential':.12,
             'originality_potential':.16,'explanation_potential':.12,'ranking_potential':.04,
             'evergreen_value':.09,'competition':.05}

    @staticmethod
    def assets(item,profile=None):
        profile=profile or store.profile()
        grant=next((r for r in profile.source_rights if r.source_url==item.get('source_url')),None)
        if grant: return [grant.asset()]
        license=str(item.get('source_license') or '').lower()
        if any(term in license for term in ('noncommercial','non-commercial','cc-by-nc','cc-by-nd')):
            return [{'source_url':item['source_url'],'source_type':'third_party','creator':item.get('creator') or 'Source creator',
                'license':item['source_license'],'rights_status':'unknown','fetched_at':now()}]
        if item.get('assets') and any(a.get('rights_status')=='commercial_use_permitted' for a in item['assets']): return item['assets']
        if profile.rights_policy=='user_managed':
            return [{'source_url':item['source_url'],'source_type':'third_party',
                     'creator':item.get('creator') or 'Source creator (see original source)',
                     'license':'unknown','rights_status':'user_managed','fetched_at':now()}]
        return item.get('assets',[])

    @classmethod
    def eligible(cls,item,profile=None):
        from app.studio.policy import RightsPolicyEngine
        profile=profile or store.profile()
        physical=(item.get('opportunity_type')!='visual_moment' or
                  (item.get('real_world_action') is True and item.get('topic_matches') is True))
        return physical and item.get('moment_verified') is True and RightsPolicyEngine.evaluate(cls.assets(item,profile),profile.rights_policy)['passed']

    KEY_DIMENSIONS=('hook_strength','visual_payoff','clarity','retention_potential','commentary_potential','audience_fit')

    @classmethod
    def qualified(cls,item,profile=None,level=None):
        """Ready to produce. With a discovery `level` the bar is that level's; without one it is the original strict bar."""
        profile=profile or store.profile();d=item.get('dimensions',{})
        if level is not None:
            values=[d.get(k) for k in cls.KEY_DIMENSIONS]
            return (cls.eligible(item,profile) and all(type(v) in (int,float) and level.dimension_min<=v<=100 for v in values)
                    and sum(values)/len(values)>=level.average_min)
        return cls.eligible(item,profile) and all(type(d.get(k)) in (int,float) and profile.quality_threshold<=d[k]<=100
            for k in ('hook_strength','visual_payoff','clarity','retention_potential','commentary_potential','audience_fit'))

    @classmethod
    def choose_format(cls,item,items):
        if not item.get('moment'):
            from app.studio.models import LEGACY_PILLARS
            if item['category'] in LEGACY_PILLARS: return 'explainer'
            raise ValueError('Select a visually verified moment before production.')
        d=item['dimensions']
        # One extraordinary standalone payoff wins over an arbitrary countdown.
        if min(d['hook_strength'],d['visual_payoff'])>=92 and not item.get('context_needed'): return 'viral_clip'
        related=[other for other in items if other.get('verified_topic')==item.get('verified_topic')
                 and other['category']==item['category'] and cls.qualified(other)]
        if len({other['source_url'] for other in related})>=10:
            return 'ranking'
        return 'commentary' if item.get('context_needed') else 'viral_clip'

    @classmethod
    def rank(cls,items):
        memory=PerformanceAnalyzer.intelligence()
        output=[]; seen=set()
        recent={title.casefold() for title in memory['recent_topics']}
        from app.studio.strictness import level as strictness_level
        shown=strictness_level()
        for item in items:
            if item['category'] not in store.profile().pillars: continue
            if item['id'] in seen or item['topic'].casefold() in recent: continue
            seen.add(item['id'])
            if item.get('opportunity_type')=='visual_moment' and not cls.eligible(item): continue
            dimensions=item['dimensions']
            weights=cls.WEIGHTS if item.get('moment') else cls.LEGACY_WEIGHTS
            if item.get('moment') and (item.get('moment_verified') is not True or (shown.require_one_second and item.get('one_second_interest') is not True)
                                        or dimensions.get('clarity',0)<shown.clarity_min): continue
            if any(type(dimensions.get(k)) not in (int,float) or not math.isfinite(dimensions[k]) or not 0<=dimensions[k]<=100 for k in weights):
                continue
            score=sum((100-dimensions[key] if key=='competition' else dimensions[key])*weight for key,weight in weights.items())
            score+=memory['performance_patterns'].get(item['category'],{}).get('priority_adjustment',0)
            count=memory['content_pillar_distribution'].get(item['category'],0)
            score-=min(8,count*1.5)
            output.append({**item,'priority':round(max(0,min(100,score)),1),
                           'production_ready':cls.qualified(item,level=shown) if item.get('moment') else False})
        return sorted(output,key=lambda item:(-item['priority'],item['id']))

    @classmethod
    def plan(cls,opportunities,at=None):
        profile=store.profile()
        local=(at or datetime.now(timezone.utc)).astimezone(ZoneInfo(profile.timezone))
        day=local.date().isoformat()
        with get_connection() as c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT data FROM daily_plans WHERE day=?',(day,)).fetchone()
            if existing:
                old=json.loads(existing['data'])
                if all(item.get('category') in profile.pillars for item in old.get('candidates',[])): return old
                for entry in old.get('candidates',[]):
                    task=c.execute('SELECT project_id,status FROM studio_tasks WHERE id=?',(entry['task_id'],)).fetchone()
                    if task and task['status']=='CREATED':
                        c.execute("UPDATE studio_tasks SET status='CANCELLED',stage='STRATEGY_CHANGED',updated_at=? WHERE id=?",(now(),entry['task_id']))
                        c.execute("UPDATE jobs SET status='CANCELLED',current_stage='Channel strategy changed' WHERE id=?",(entry['task_id'],))
                        c.execute("UPDATE projects SET status='CANCELLED' WHERE id=?",(task['project_id'],))
            ordered=[item for item in cls.rank(opportunities) if item['priority']>=profile.quality_threshold
                     and (cls.qualified(item,profile) if item.get('moment') else item.get('category') not in PILLARS)]
            finalists=ordered[:5]
            candidates=[]; pillars=set()
            used_sources=set()
            def select(item):
                format=cls.choose_format(item,opportunities)
                related=([other for other in opportunities if other.get('verified_topic')==item.get('verified_topic')
                    and other['category']==item['category'] and cls.qualified(other,profile)] if format=='ranking' else [item])
                sources={other['source_url'] for other in related}
                if sources & used_sources:return False
                candidates.append({**item,'chosen_format':format});used_sources.update(sources);return True
            for item in finalists:
                if item['source_url'] in used_sources: continue
                if item['category'] in pillars: continue
                if select(item):pillars.add(item['category'])
                if len(candidates)==3: break
            for item in finalists:
                if len(candidates)==3: break
                if item['source_url'] not in used_sources:select(item)
            entries=[]
            for i,item in enumerate(candidates):
                hour=profile.publishing_hours[min(i,len(profile.publishing_hours)-1)]
                planned=local.replace(hour=hour,minute=0,second=0,microsecond=0)
                if planned<local: planned=local+timedelta(minutes=10+i*420)
                format=item['chosen_format']
                task=store.enqueue(item['id'],format,day,planned.astimezone(timezone.utc).isoformat(),connection=c)
                entries.append({'task_id':task['id'],'topic':item['topic'],'category':item['category'],'priority':item['priority'],'format':format})
            value={'date':day,'candidates':entries,'finalists':[item['id'] for item in finalists],
                   'publication_limit':profile.publication_limit,'strategy':profile.positioning,
                   'reason':'Up to three visually strong candidates with documented rights; publish only after all gates pass. No quota fill.'}
            c.execute('INSERT INTO daily_plans VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET data=excluded.data,created_at=excluded.created_at',(day,json.dumps(value),now()))
        return value


class RankingEngine:
    WEIGHTS={'topic_relevance':.22,'visual_appeal':.12,'novelty':.12,'surprise':.10,
             'usefulness':.12,'story_value':.12,'technical_credibility':.12,'audience_fit':.08}

    @classmethod
    def score(cls,item):
        dimensions=item['dimensions']
        if any(type(dimensions.get(k)) not in (int,float) or not math.isfinite(dimensions[k]) or not 0<=dimensions[k]<=100 for k in cls.WEIGHTS):
            raise ValueError('Rankings need bounded evidence-backed dimensions.')
        return sum(dimensions[k]*v for k,v in cls.WEIGHTS.items())

    @classmethod
    async def rerank(cls,items,provider):
        """Weighted shortlist, then adjacent evidence-based pairwise decisions."""
        from app.ai.ranking_verifier import parse_object
        ordered=sorted(items,key=lambda item:(-cls.score(item),item['id']))
        # Insertion comparisons allow a lower weighted finalist to move all the
        # way to #1; one adjacent pass could leave the strongest item at #2.
        ranked=[]
        for candidate in ordered:
            position=len(ranked)
            for i,other in enumerate(ranked):
                decision=parse_object(await provider.generate_text('Choose the stronger editorial technology story using ONLY this evidence. '
                    'Return JSON {"winner_id":"id","reason":"specific comparative evidence"}. '
                    'This is a subjective editorial ordering, not a factual best-product claim.\n'+json.dumps([candidate,other])))
                if decision.get('winner_id') not in (candidate['id'],other['id']) or len(decision.get('reason',''))<30:
                    raise ValueError('Pairwise ranking did not provide comparative evidence.')
                candidate.setdefault('pairwise_evidence',[]).append(decision)
                if decision['winner_id']==candidate['id']:
                    position=i;break
            ranked.insert(position,candidate)
        ordered=ranked
        return [{**item,'assigned_rank':i+1,'weighted_score':round(cls.score(item),2)} for i,item in enumerate(ordered)]

    @classmethod
    async def research_finalists(cls,provider,research):
        from app.ai.ranking_verifier import parse_object
        candidates=research['ranking_candidates']
        prompt=('Evaluate these three technology stories using only their primary-source statements. Ratings are editorial dimensions, not performance facts or viral probabilities. '
                'Return JSON {"candidates":[{"id":"supplied id","dimensions":{"topic_relevance":80,"visual_appeal":80,"novelty":80,"surprise":80,"usefulness":80,"story_value":80,"technical_credibility":80,"audience_fit":80},"reason":"specific sourced explanation"}]}. '
                'Every ID exactly once. Do not treat marketing claims as independently verified.\n'+json.dumps(candidates))
        value=parse_object(await provider.generate_text(prompt))
        decisions=value.get('candidates',[])
        by_id={row.get('id'):row for row in decisions}
        if len(by_id)!=3 or set(by_id)!={c['id'] for c in candidates}: raise ValueError('Ranking analysis omitted or invented a researched candidate.')
        complete=[]
        for candidate in candidates:
            row=by_id[candidate['id']]
            if len(row.get('reason',''))<30: raise ValueError('Ranking dimensions require a sourced editorial explanation.')
            result={**candidate,'dimensions':row['dimensions'],'reason':row['reason']}
            cls.score(result);complete.append(result)
        return await cls.rerank(complete,provider)
