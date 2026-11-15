import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
from skills_manager import Skills

def reply(text): return {'choices':[{'message':{'content':text}}]}

class RoutingTests(unittest.TestCase):
 def test_local_automatic_link_and_use(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'game-builder'; root.mkdir(); (root/'SKILL.md').write_text('# Build a game')
   a=Agent(d,'http://localhost','test','auto'); a.skills=Skills(Path(d)/'app')
   entries=[{'name':'game-builder','description':'Build games','path':str(root),'registered':False}]
   with patch('skill_router.catalog',return_value=entries),patch.object(a,'api',return_value=reply('{"ids":[0],"reason":"creazione gioco"}')),patch('builtins.input',side_effect=AssertionError('No prompt expected')):
    a.skill_router.run(a,'crea un gioco',lambda _:None)
   self.assertEqual(len(a.active_skills),1)
   self.assertIn('# Build a game',next(iter(a.active_skills.values())))
 def test_web_fallback_and_cache(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto')
   with patch('skill_router.catalog',return_value=[]),patch.object(a,'api',return_value=reply('{"ids":[],"web_query":"astronomy visualization SKILL.md github"}')),patch('skill_router.search',return_value={'results':[{'url':'https://example.com/SKILL.md'}]}) as search:
    a.skill_router.run(a,'task',lambda _:None); a.skill_discovery=''
    a.skill_router.run(a,'task',lambda _:None)
    search.assert_called_once()
   self.assertIn('example.com',a.skill_discovery)
   self.assertFalse(a.active_skills)
 def test_greeting_no_request(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto')
   with patch.object(a,'api') as api:
    a.skill_router.run(a,'ciao!',lambda _:None); api.assert_not_called()
 def test_bad_selection_does_not_link(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto')
   with patch('skill_router.catalog',return_value=[]),patch.object(a,'api',return_value=reply('{"ids":[999]}')):
    a.skill_router.run(a,'task',lambda _:None)
   self.assertFalse(a.active_skills)
 def test_task_switch_removes_auto_skill(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.active_skills={'old':'old task','manual':'user choice'}; a.skill_router.automatic={'old'}
   with patch('skill_router.catalog',return_value=[]),patch.object(a,'api',return_value=reply('{"ids":[]}')):
    a.skill_router.run(a,'new task',lambda _:None)
   self.assertEqual(a.active_skills,{'manual':'user choice'})

if __name__=='__main__': unittest.main()
