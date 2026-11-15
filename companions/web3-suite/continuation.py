"""Recognize continuation without replacing the task's explicit instructions."""
import re

def is_continuation(text):
    return bool(re.fullmatch(r'\s*(procedi|continua|prosegui|vai avanti|continue|go on|riprendi)[.!\s]*',text or '',re.I))

def instruction_index(messages):
    indices=[i for i,m in enumerate(messages) if m.get('role')=='user']
    for i in reversed(indices):
        if not is_continuation(messages[i].get('content')): return i
    return indices[-1] if indices else 0
