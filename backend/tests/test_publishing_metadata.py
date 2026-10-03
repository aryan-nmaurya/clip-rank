from app.core import database
from app.publishing import youtube
from app.publishing.metadata import scene_tags,tag_characters


def make_clip():
    database.create_project('p','ranking','Requested parkour fails',{'topic':'Parkour fails'})
    database.create_job('j','p')
    database.create_or_update_clip('b','p','j','Unrelated search title',status='READY')


def test_ranking_metadata_uses_uploaded_variant_and_real_rank_order(isolated_app):
    make_clip()
    database.update_project('p',result_data={'variants':[
        {'clip_id':'a','moments':[{'assigned_rank':1,'observed_action':'A dog catches a ball.'}]},
        {'clip_id':'b','moments':[
            {'assigned_rank':1,'creator':'Creator One','observed_action':'An athlete climbs a wall, performs a backflip and lands on the platform.'},
            {'assigned_rank':5,'creator':'Creator Five','observed_action':'A runner clears a rail and lands on both feet.'}]}]})
    result=youtube.description_preview('b')
    assert result['scene_description'].startswith('This Short ranks these moments:\n#5: A runner clears a rail')
    assert result['scene_description'].index('#5:')<result['scene_description'].index('#1:')
    assert 'dog' not in result['description'] and 'unrelated search' not in result['description'].lower()
    assert 'Creator One' in result['description'] and 'Creator Five' in result['description']
    assert len(result['tags'])==32 and len(set(result['tags']))==32 and result['tag_characters']<=500
    assert 'fails' not in result['tags'] and 'dog' not in result['tags']
    assert youtube.description_preview('b',result['description'])==result


def test_standalone_metadata_describes_finished_story_without_borrowing_other_clips(isolated_app):
    make_clip()
    database.update_project('p',result_data={'moments':[
        {'clip_id':'a','observed_action':'A cyclist crashes into a fence.'},
        {'clip_id':'b','creator':'Stunt performer','script':{'observed_action':
            'A woman runs toward a wooden wall, climbs it, performs a backflip, and lands before an audience.'}}]})
    result=youtube.description_preview('b')
    assert 'wooden wall' in result['description'] and 'backflip' in result['description']
    assert 'cyclist' not in result['description'] and 'crashes' not in result['tags'] and 'fails' not in result['tags']
    assert len(result['tags'])==32 and tag_characters(result['tags'])<=500
    assert result['description'].startswith('\n'.join(['.']*13)+'\n\nCredits:')


def test_tag_budget_includes_commas_and_multiword_quotes():
    assert tag_characters(['backflip','wall climb'])==8+1+10+2
    for scene in ('Jump.', 'A cat jumps over a sofa.',
                  'The performer completes the aerial flip after scaling the wall.',
                  'A football goalkeeper dives across the goal and catches the ball with both hands.'):
        tags=scene_tags(scene)
        assert len(tags)==32 and len(set(tags))==32 and tag_characters(tags)<=500
        assert all(tag and '<' not in tag and '>' not in tag and '#' not in tag for tag in tags)
