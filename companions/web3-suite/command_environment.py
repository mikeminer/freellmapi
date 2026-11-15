"""Resolve locally installed Android tools for every isolated command process."""
import os
from pathlib import Path


def environment():
    env=dict(os.environ)
    from seeker_adb import find_adb
    adb=find_adb()
    if adb:
        env['PATH']=str(Path(adb).parent)+os.pathsep+env.get('PATH','')
        env.setdefault('ANDROID_HOME',str(Path(adb).parent.parent))
    if not env.get('JAVA_HOME'):
        candidates=sorted(Path(os.getenv('ProgramFiles','C:/Program Files')).glob('Microsoft/jdk-17*'))
        if candidates:env['JAVA_HOME']=str(candidates[-1])
    return env
