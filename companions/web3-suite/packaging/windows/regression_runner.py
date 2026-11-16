"""Execute installed-source unit fixtures with private Python and no WAN access."""
import argparse,ipaddress,json,os,sys,unittest
from pathlib import Path

MODULES='test_bundled_skills test_windows_installer test_payload_privacy test_suite_setup test_skills test_skill_router test_desktop_connectors test_memory_write_policy test_memory test_brain_memory test_shared_desktop test_desktop_languages_brand test_financial_allocation test_financial_focus test_trade_proposal test_mobile_execution_reliability test_hyperliquid_execution test_hyperliquid test_hyperliquid_leverage test_hyperliquid_key_only test_hyperliquid_protection test_hyperliquid_reconciliation test_hyperliquid_analysis test_hyperliquid_menu test_seeker_latest_session test_seeker_session_recovery test_remote_access test_remote_pairing test_session_routing test_market_access test_market_request_regression test_financial_native_recovery test_financial_execution_recovery test_intent_contract test_model_picker'.split()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--suite-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    sys.path.insert(0,str(a.suite_root/'launcher'));import suite;suite.configure(a.suite_root)
    fixture_root=a.output.parent/'regression-fixtures';fixture_root.mkdir(exist_ok=True)
    os.environ['FREELLMAPI_HOME']=str(fixture_root/'home');os.environ['APPDATA']=str(fixture_root/'appdata')
    # Unit fixture catalogs must be empty. The real installed 894-file bundle
    # is independently exercised by Setup registration, doctor and offline UI QA.
    os.environ['FREELLMAPI_SUITE_ROOT']=str(fixture_root/'empty-unit-catalog')
    os.environ.pop('FREELLMAPI_READ_ONLY',None)
    blocked=[]
    def no_wan(event,args):
        if event!='socket.connect':return
        target=args[1]
        if not isinstance(target,tuple):return
        host=str(target[0])
        try:local=ipaddress.ip_address(host).is_loopback
        except ValueError:local=host=='localhost'
        if not local:blocked.append(host);raise PermissionError('Installer unit fixtures forbid external network connections')
    sys.addaudithook(no_wan)
    loaded=unittest.defaultTestLoader.loadTestsFromNames(MODULES);unique={}
    def collect(test):
        if isinstance(test,unittest.TestSuite):
            for item in test:collect(item)
        else:unique.setdefault(test.id(),test)
    collect(loaded)
    result=unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(unique.values()))
    summary={'status':'PASS' if result.wasSuccessful() and not blocked else 'FAIL','tests':result.testsRun,
        'unique_cases':len(unique),'failures':[case.id() for case,_ in result.failures],
        'errors':[case.id() for case,_ in result.errors],'skipped':len(result.skipped),'outbound_connections_blocked':len(blocked),
        'private_python':Path(sys.prefix).resolve()==(a.suite_root/'runtime/python').resolve(),
        'scope':'Installed sources and runtime; isolated unit fixtures and localhost HTTP only. Financial transports are mocks, not live order acceptance.'}
    a.output.write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary))
    return 0 if summary['status']=='PASS' and summary['private_python'] else 1

if __name__=='__main__':raise SystemExit(main())
