"""Explicit harmless probes; results apply only to the returned model/request."""
import json
from pathlib import Path
import tempfile
import secrets
import re
from datetime import datetime,timezone


def recognizes(content,expected):
    # Some providers include explanation despite "only the code". Match an exact,
    # bounded random code that was present only in the pixels, never in the text prompt.
    return re.search(r'(?<![A-Z0-9])'+re.escape(expected)+r'(?![A-Z0-9])',content.upper()) is not None


def probe(agent):
    from autonomy import BudgetExceeded
    report={'checked_utc':datetime.now(timezone.utc).isoformat(),
            'note':'Prove indipendenti; auto può usare modelli diversi. Nessuna azione reale eseguita.'}
    for name,check in [('vision',vision_probe),('tool_calls',tool_probe)]:
        try: report[name]=check(agent)
        except BudgetExceeded: raise
        except Exception as exc: report[name]={'model_requested':agent.model,'passed':None,'error':str(exc)[:1200]}
    home=Path(agent.extensions.home); home.mkdir(parents=True,exist_ok=True)
    path=home/'model-observations.json'; tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(agent.model_observations,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)
    return report


def vision_probe(agent):
    from PIL import Image,ImageDraw,ImageFont
    from vision_input import attachment
    expected=secrets.token_hex(3).upper()
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'probe.png'
        image=Image.new('RGB',(500,160),'white'); draw=ImageDraw.Draw(image)
        try: font=ImageFont.truetype('C:/Windows/Fonts/consola.ttf',65)
        except OSError: font=ImageFont.load_default(size=48)
        draw.text((25,40),expected,fill='black',font=font); image.save(path)
        response=agent.api('chat/completions',{'model':agent.model,'max_tokens':100,
            'messages':[{'role':'user','content':[{'type':'text','text':'Return only the six characters printed in this image, no other text.'},attachment(path)]}]})
    actual=response.get('model') or 'non comunicato'
    content=response['choices'][0]['message'].get('content') or ''
    visual=recognizes(content,expected)
    agent.model_observations.setdefault(actual,{}).update({'vision_probe_passed':visual,'vision_probe_note':'Single generated text image, not a guarantee for other images or auto routes.'})
    return {'model':actual,'passed':visual,'expected':expected,'response':content[:500],
            'criterion':'Exact randomly generated code found in response; code appeared only in image pixels.'}


def tool_probe(agent):
    tool={'type':'function','function':{'name':'probe_echo','description':'Return the requested value; diagnostic only.','parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}}
    response=agent.api('chat/completions',{'model':agent.model,'max_tokens':100,
        'messages':[{'role':'user','content':'Call probe_echo with value freellmapi-test.'}],
        'tools':[tool],'tool_choice':{'type':'function','function':{'name':'probe_echo'}}})
    tool_actual=response.get('model') or 'non comunicato'; valid=False
    for call in response['choices'][0]['message'].get('tool_calls',[]):
        try: valid=call['function']['name']=='probe_echo' and json.loads(call['function']['arguments'])=={'value':'freellmapi-test'}
        except (ValueError,KeyError,TypeError): continue
        if valid: break
    agent.model_observations.setdefault(tool_actual,{}).update({'tool_probe_passed':valid})
    return {'model':tool_actual,'passed':valid}
