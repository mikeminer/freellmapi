"""Recognize the installed audit command without treating its output path as new work."""
import re

def is_audit(command):
    return bool(re.search(r'(?i)(?:^|[\\/\s"\x27])qa_audit_runner\.py(?:[\s"\x27]|$)',command))

def action_arguments(args):
    if not is_audit(args.get('command','')): return args
    command=re.sub(r'(?i)--output(?:\s+|=)(?:"[^"]*"|\x27[^\x27]*\x27|[^\s;|]+)','--output <artifact>',args['command'])
    # Increasing the time limit is a recovery step, changing the output folder is not.
    return {**args,'command':command}

def timeout_for(command,requested=None):
    return requested if requested is not None else (600 if is_audit(command) else 60)
