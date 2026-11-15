"""Actionable exact-edit errors. Never apply fuzzy or guessed replacements."""
import difflib
import json
from reliable_runtime import digest


class EditRecovery:
    def __init__(self): self.failures={}

    def replacement(self,path,source,needle,replacement):
        key=(str(path),digest(source.encode()),needle)
        if key in self.failures:
            raise ValueError(json.dumps({'code':'edit_strategy_repeated','path':str(path),'changed':False,
                'message':'Questa sostituzione è già fallita sullo stesso contenuto. Non ripeterla cambiando soltanto new.',
                'next_action':'Usa una porzione più piccola, copiata dal contenuto letto, che compaia una sola volta. Se non puoi farlo, segnala questo file bloccato e continua gli altri compiti.'},ensure_ascii=False))
        count=source.count(needle) if needle else 0
        if not needle or count!=1:
            self.failures[key]=count
            lines=source.splitlines(); wanted=needle.splitlines()
            anchor=wanted[0] if wanted else ''
            matches=difflib.get_close_matches(anchor,lines,n=3,cutoff=.5)
            excerpts=[]
            for match in matches:
                at=lines.index(match); excerpts.append({'start':at+1,'text':'\n'.join(lines[max(0,at-2):at+8])[:1800]})
            raise ValueError(json.dumps({'code':'edit_text_not_unique','path':str(path),'changed':False,'occurrences':count,
                'message':'old deve corrispondere esattamente una volta. Nessuna modifica applicata.',
                'next_tool':'read_file','arguments':{'path':str(path)},'observed_excerpts':excerpts,
                'next_action':'Rileggi il file, copia un blocco reale più piccolo e univoco. Non inventare intestazioni successive; non usare write_file per aggirare il conflitto.'},ensure_ascii=False))
        return source.replace(needle,replacement,1)
