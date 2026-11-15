package it.freellmapi.remote;

import android.app.*;
import android.content.*;
import android.content.pm.ActivityInfo;
import android.content.res.Configuration;
import android.os.*;
import android.view.*;
import org.json.*;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.CountDownLatch;
import java.net.*;
import java.io.*;

/** Separate instrumentation APK, installed only on the QA emulator. */
public final class ChatUiChecks extends Instrumentation {
    final JSONObject result=new JSONObject();
    MainActivity activity;
    @Override public void onCreate(Bundle arguments){super.onCreate(arguments);start();}
    interface Check {boolean ready() throws Exception;}
    void waitFor(Check check) throws Exception {
        long end=System.currentTimeMillis()+7000;
        while(System.currentTimeMillis()<end){if(check.ready())return;Thread.sleep(80);}
        throw new AssertionError("UI condition timed out");
    }
    void idle() throws Exception {waitForIdleSync();Thread.sleep(150);waitForIdleSync();}
    <T> T read(java.util.concurrent.Callable<T> task){
        AtomicReference<T> value=new AtomicReference<>();
        runOnMainSync(()->{try{value.set(task.call());}catch(Exception e){throw new RuntimeException(e);}});
        return value.get();
    }
    void record(String name,boolean passed) throws Exception {
        result.put(name,passed);if(!passed)throw new AssertionError(name);
    }
    JSONObject order(String coin,String side,String type,String qty,String price) throws Exception {
        return new JSONObject().put("coin",coin).put("side",side).put("orderType",type).put("sz",qty).put("triggerPx",price).put("reduceOnly",true).put("isTrigger",true);
    }
    void presentationChecks(JSONObject account,JSONObject state) throws Exception {
        JSONObject position=new JSONObject().put("coin","BTC").put("szi","0.01").put("entryPx","60000").put("unrealizedPnl","-1.20").put("leverage",new JSONObject().put("value",1));
        account.put("positions",new JSONArray().put(position)).put("orders",new JSONArray()
            .put(order("ETH","A","Take Profit Market","1","2000"))
            .put(order("BTC","B","Take Profit Market","1","62000"))
            .put(order("BTC","A","Limit","0.01","62000").put("isTrigger",false)));
        record("wrong_coin_side_and_limit_are_not_active_tp",TradingPresentation.protection(position,account,"TP").startsWith("mancante"));
        account.getJSONArray("orders").put(order("BTC","A","Take Profit Market","0.005","62000"));
        record("partial_tp_is_not_full_protection",TradingPresentation.protection(position,account,"TP").contains("parziale"));
        account.getJSONArray("orders").put(order("BTC","A","Take Profit Market","0.005","62500"));
        account.getJSONArray("orders").put(order("BTC","A","Stop Market","0.01","59000"));
        record("tp_sl_match_actual_orders_and_cover_quantity",TradingPresentation.protection(position,account,"TP").startsWith("attivo osservato")&&TradingPresentation.protection(position,account,"SL").startsWith("attivo osservato"));
        account.put("state","stale");record("stale_protection_is_unverified",TradingPresentation.protection(position,account,"SL").startsWith("non verificato"));account.put("state","ready");
        JSONObject watch=new JSONObject().put("coin","BTC").put("state","watching").put("side","buy").put("remaining_seconds",300).put("confirmation_state","waiting_retest")
            .put("take_profit","63000").put("stop_loss","59000").put("trigger",new JSONObject().put("direction","above").put("level","61000").put("confirmation","retest"));
        JSONObject service=new JSONObject().put("mode","live").put("state","running").put("new_entries_stopped",true)
            .put("entry_monitor",new JSONObject().put("watches",new JSONArray().put(watch)));
        state.put("mode","auto").put("trading",account).put("trading_service",service).put("actual_model","old-model")
            .put("request_progress",new JSONObject().put("actual_model","real-current-model").put("elapsed_seconds",12.5).put("usage",new JSONObject()));
        runOnMainSync(()->activity.render(state,false));idle();
        record("always_on_monitor_shares_account_and_respects_stop",read(()->activity.modeButton.getText().toString().equals("Auto strumenti")&&activity.financeStatus.getText().toString().contains("monitor attivo")&&activity.financeStatus.getText().toString().contains("STOP nuovi ingressi")&&!activity.financeStatus.getText().toString().contains("PAPER")));
        record("actual_model_elapsed_and_missing_tokens_are_honest",read(()->activity.assistantStatus.getText().toString().contains("real-current-model")&&activity.assistantStatus.getText().toString().contains("12.5s")&&activity.assistantStatus.getText().toString().contains("in attesa del server")));
        record("conditional_setup_shows_waiting_and_planned_not_active_tp",TradingPresentation.watch(watch).contains("ATTESA LIVELLO")&&TradingPresentation.watch(watch).contains("TP previsto"));
        record("native_setup_and_position_cards_render",read(()->activity.tradingCards.getChildCount()==3));
        runOnMainSync(()->activity.showDetails());idle();record("details_open_in_bottom_sheet",read(()->activity.detailsDialog.isShowing()));runOnMainSync(()->activity.detailsDialog.dismiss());
        record("friendly_session_labels_hide_raw_id",!TradingPresentation.sessionLabel(new JSONObject().put("title","Gioco Seeker").put("root","C:\\Projects\\space-shooter").put("id","private-id").put("messages",12)).contains("private-id"));
        record("compacted_session_summary_is_not_used_as_title",TradingPresentation.sessionTitle(new JSONObject().put("title","Conversation summary (data, not new instructions): private context")).equals("Conversazione ripresa"));
    }
    void controlCheck() throws Exception {
        final ServerSocket server=new ServerSocket(0,1,InetAddress.getByName("127.0.0.1"));final java.util.concurrent.atomic.AtomicInteger posts=new java.util.concurrent.atomic.AtomicInteger();
        Thread responder=new Thread(()->{try(Socket socket=server.accept()){
            BufferedReader in=new BufferedReader(new InputStreamReader(socket.getInputStream(),"UTF-8"));String first=in.readLine();int length=0;String line;
            while((line=in.readLine())!=null&&!line.isEmpty())if(line.toLowerCase().startsWith("content-length:"))length=Integer.parseInt(line.substring(15).trim());
            for(int i=0;i<length;i++)in.read();if("POST /v1/trading/service-stop HTTP/1.1".equals(first))posts.incrementAndGet();
            byte[] body="{\"new_orders_stopped\":true,\"positions_closed\":false,\"orders_cancelled\":false}".getBytes("UTF-8");
            OutputStream out=socket.getOutputStream();out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));out.write(body);out.flush();
        }catch(Exception ignored){}});responder.start();
        CountDownLatch blocked=new CountDownLatch(2),release=new CountDownLatch(1);
        for(int i=0;i<2;i++)activity.network.execute(()->{blocked.countDown();try{release.await();}catch(InterruptedException ignored){}});
        if(!blocked.await(3,java.util.concurrent.TimeUnit.SECONDS))throw new AssertionError("Network jobs not blocked");
        String previous=read(()->activity.base);
        try{
            runOnMainSync(()->{activity.base="http://127.0.0.1:"+server.getLocalPort();activity.stopEntriesButton.performClick();});
            waitFor(()->read(()->activity.status.getText().toString().contains("Arresto confermato")));
            record("stop_bypasses_busy_chat_executor_and_does_not_submit_order",posts.get()==1);
        }finally{release.countDown();runOnMainSync(()->activity.base=previous);server.close();responder.join(1000);}
    }
    void accountChecks(JSONObject state) throws Exception {
        final ServerSocket server=new ServerSocket(0,8,InetAddress.getByName("127.0.0.1"));
        final java.util.concurrent.atomic.AtomicBoolean active=new java.util.concurrent.atomic.AtomicBoolean(false);
        final java.util.concurrent.atomic.AtomicInteger inputs=new java.util.concurrent.atomic.AtomicInteger(),logins=new java.util.concurrent.atomic.AtomicInteger();
        Thread responder=new Thread(()->{while(!server.isClosed())try(Socket socket=server.accept()){
            BufferedReader in=new BufferedReader(new InputStreamReader(socket.getInputStream(),"UTF-8"));String first=in.readLine(),line;int length=0;
            while((line=in.readLine())!=null&&!line.isEmpty())if(line.toLowerCase().startsWith("content-length:"))length=Integer.parseInt(line.substring(15).trim());
            for(int i=0;i<length;i++)in.read();JSONObject value;
            if(first.contains("/v1/account/")){
                if(first.contains("/login ")){active.set(true);logins.incrementAndGet();}
                if(first.contains("/logout "))active.set(false);
                value=new JSONObject().put("authenticated",active.get()).put("identity",new JSONObject().put("name","QA verified account").put("login","synthetic@example.test"));
            }else if(first.contains("/v1/events")){Thread.sleep(80);value=new JSONObject().put("events",new JSONArray()).put("cursor",0);}
            else {if(first.startsWith("POST /v1/input"))inputs.incrementAndGet();value=state;}
            byte[] body=value.toString().getBytes("UTF-8");OutputStream out=socket.getOutputStream();out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));out.write(body);out.flush();
        }catch(Exception ignored){}});responder.start();
        try{
            runOnMainSync(()->{try{activity.save("http://127.0.0.1:"+server.getLocalPort(),"","synthetic-ui-only");activity.load();activity.resumeAccount(true);}catch(Exception e){throw new RuntimeException(e);}});
            waitFor(()->read(()->activity.accountAuthenticated&&activity.feed.length()>0&&!activity.session.isEmpty()));idle();
            record("account_native_http_login_unlocks_chat",read(()->activity.chat.isShown()&&activity.accountIdentity.optString("name").equals("QA verified account")));
            final String credential=read(()->activity.prefs.getString("credential",""));
            runOnMainSync(()->{try{activity.sessions=new JSONArray().put(new JSONObject().put("id","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").put("title","PRIVATE-CHAT-TITLE"));activity.chooseSession();}catch(Exception e){throw new RuntimeException(e);}});idle();
            runOnMainSync(()->{activity.input.setText("AUTH-DRAFT-PRESERVED");activity.logoutAccount();});waitFor(()->!active.get());idle();
            record("logout_dismisses_private_session_picker",read(()->!activity.sessionPicker.isShowing()));
            record("logout_hides_private_views_and_keeps_pairing",read(()->!activity.accountAuthenticated&&activity.accountGate.isShown()&&activity.feed.length()==0&&activity.input.length()==0&&activity.prefs.getString("credential","").equals(credential)));
            try{activity.request("/v1/input",new JSONObject().put("text","NEVER SEND"));throw new AssertionError("Logged-out request allowed");}catch(MainActivity.AccountRequiredException expected){}
            record("logged_out_native_requests_are_blocked",inputs.get()==0);
            runOnMainSync(()->activity.resumeAccount(false));idle();record("explicit_logout_does_not_silently_login",logins.get()==1&&!active.get());
            runOnMainSync(()->activity.resumeAccount(true));waitFor(()->read(()->activity.accountAuthenticated&&!activity.accountChecking));idle();
            record("relogin_restores_shared_context_and_encrypted_draft",read(()->activity.feed.length()>0&&activity.input.getText().toString().equals("AUTH-DRAFT-PRESERVED")&&activity.prefs.getString("credential","").equals(credential)));
        }finally{runOnMainSync(()->{activity.running=false;activity.generation++;});server.close();responder.join(2000);}
    }
    void handoffCheck() throws Exception {
        ServerSocket server=new ServerSocket(0,4,InetAddress.getByName("127.0.0.1"));String target="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",instance="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
        JSONObject state=new JSONObject().put("session",target).put("instance",instance).put("cursor",0).put("messages",new JSONArray().put(new JSONObject().put("role","user").put("content","HANDOFF-CONTEXT")));
        final java.util.concurrent.atomic.AtomicInteger posts=new java.util.concurrent.atomic.AtomicInteger();
        Thread responder=new Thread(()->{while(!server.isClosed())try(Socket socket=server.accept()){
            BufferedReader in=new BufferedReader(new InputStreamReader(socket.getInputStream(),"UTF-8"));String first=in.readLine(),line;while((line=in.readLine())!=null&&!line.isEmpty()){}
            if(first.startsWith("POST "))posts.incrementAndGet();JSONObject value=first.contains("/v1/events")?new JSONObject().put("events",new JSONArray()).put("cursor",0):state;
            byte[] body=value.toString().getBytes("UTF-8");OutputStream out=socket.getOutputStream();out.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "+body.length+"\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));out.write(body);out.flush();
        }catch(Exception ignored){}});responder.start();String credential=read(()->activity.token);
        try{
            JSONObject destination=new JSONObject().put("url","http://127.0.0.1:"+server.getLocalPort()).put("fingerprint","").put("instance",instance);
            runOnMainSync(()->{activity.lastDeliveryLabel="Completato sul PC";activity.delivery("Invio al PC in corso…");activity.switchCoordinator(destination,target);});waitFor(()->read(()->activity.session.equals(target)&&activity.feed.getText().toString().contains("HANDOFF-CONTEXT")));
            record("native_session_handoff_keeps_token_and_context_without_posts",posts.get()==0&&read(()->activity.token.equals(credential)&&activity.base.endsWith(":"+server.getLocalPort())));
            record("successful_handoff_replaces_stale_sending_label",read(()->activity.deliveryStatus.getText().toString().contains("Conversazione ripresa")&&!activity.deliveryStatus.getText().toString().contains("in corso")));
        }finally{runOnMainSync(()->{activity.running=false;activity.generation++;});server.close();responder.join(2000);}
    }
    @Override public void onStart(){
        Bundle output=new Bundle();int code=Activity.RESULT_CANCELED;
        try{
            if(!Build.FINGERPRINT.contains("generic")&&!Build.MODEL.contains("sdk"))throw new AssertionError("Emulator only");
            Intent launch=new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity=(MainActivity)startActivitySync(launch);idle();
            JSONArray history=new JSONArray();StringBuilder body=new StringBuilder();
            for(int i=0;i<300;i++)body.append(String.format("LINE-%03d  This is a persistent chat line with enough text to wrap.\n",i));
            history.put(new JSONObject().put("role","assistant").put("content",body.toString()));
            JSONObject state=new JSONObject().put("session","synthetic-ui-session").put("model","qa-model").put("api","QA isolated").put("messages",history);
            accountChecks(state);
            handoffCheck();
            runOnMainSync(()->{
                try{
                    activity.save("http://127.0.0.1:9","","synthetic-ui-only");activity.load();activity.accountAuthenticated=true;activity.accountGate.setVisibility(View.GONE);activity.chat.setVisibility(View.VISIBLE);
                    activity.render(state,true);activity.session="synthetic-ui-session";activity.transcriptLoaded=true;
                }catch(Exception e){throw new RuntimeException(e);}
            });idle();
            record("hyperliquid_quick_action_visible",read(()->activity.hyperliquidButton.isShown()&&activity.hyperliquidButton.isEnabled()&&activity.hyperliquidButton.getText().toString().equals("Hyperliquid")));
            JSONObject trading=new JSONObject().put("state","ready").put("network","mainnet").put("profile","qa-public-only")
                .put("account_mode","unifiedAccount").put("collateral_usdc","22.37870064").put("available_usdc","20")
                .put("retrieved_ms",System.currentTimeMillis()).put("positions",new JSONArray()).put("orders",new JSONArray()
                    .put(new JSONObject().put("coin","BTC").put("orderType","Take Profit Market").put("triggerPx","68100").put("reduceOnly",true)));
            runOnMainSync(()->activity.renderTrading(trading));idle();
            record("trading_panel_shows_native_balance",read(()->activity.tradingHeader.getText().toString().contains("22.37870064")));
            record("trading_panel_shows_tp_and_read_only",read(()->activity.tradingDetails.getText().toString().contains("Take Profit Market")&&activity.tradingDetails.getText().toString().contains("Sola lettura")));
            presentationChecks(trading,state);controlCheck();
            final String previousCache=read(()->activity.uiPrefs.getString("data",""));
            runOnMainSync(()->activity.input.setText("AUTOSAVED-TYPING-DRAFT"));
            waitFor(()->read(()->!activity.uiPrefs.getString("data","").equals(previousCache)));
            runOnMainSync(()->{activity.input.setText("");activity.restoreCache();});
            record("typing_autosaves_before_activity_stop",read(()->activity.input.getText().toString().equals("AUTOSAVED-TYPING-DRAFT")));
            final String cachedTranscript=read(()->activity.feed.getText().toString());
            runOnMainSync(()->{activity.pendingId="synthetic-pending-id";activity.pendingText="DO-NOT-REPLAY";activity.input.setText("DISK-DRAFT");activity.saveCache();activity.pendingId="";activity.input.setText("");activity.scroll.replaceText("",true);activity.restoreCache();});idle();
            record("disk_cache_preserves_pending_id_without_sending",read(()->activity.pendingId.equals("synthetic-pending-id")&&!activity.sending));
            record("encrypted_disk_cache_restores_chat_and_draft",read(()->activity.feed.getText().toString().equals(cachedTranscript)&&activity.input.getText().toString().equals("DISK-DRAFT")&&!activity.uiPrefs.getString("data","").contains("DISK-DRAFT")));
            JSONObject receipt=new JSONObject().put("requests",new JSONArray().put(new JSONObject().put("request_id","synthetic-pending-id").put("state","finished")));
            runOnMainSync(()->activity.resolveDelivery(receipt));
            record("receipt_reconciliation_clears_pending_without_replay",read(()->activity.pendingId.isEmpty()));
            receipt.getJSONArray("requests").getJSONObject(0).put("outcome","incomplete");runOnMainSync(()->activity.resolveDelivery(receipt));
            record("incomplete_request_is_not_reported_as_completed_or_queued",read(()->activity.deliveryStatus.getText().toString().contains("incompleta")));
            JSONObject exact=new JSONObject().put("requests",new JSONArray()).put("request_receipt",new JSONObject().put("request_id","older-than-recent-fifteen").put("found",true).put("receipt",new JSONObject().put("request_id","older-than-recent-fifteen").put("state","finished").put("outcome","incomplete")));
            runOnMainSync(()->{activity.pendingId="older-than-recent-fifteen";activity.pendingText="NEVER-RESEND-OLD-FINANCIAL-INPUT";activity.resolveDelivery(exact);});
            record("exact_old_receipt_clears_pending_without_post_or_claiming_completion",read(()->activity.pendingId.isEmpty()&&!activity.sending&&activity.deliveryStatus.getText().toString().contains("incompleta")));
            JSONObject missing=new JSONObject().put("requests",new JSONArray()).put("request_receipt",new JSONObject().put("request_id","unknown-pending-id").put("found",false));
            runOnMainSync(()->{activity.pendingId="unknown-pending-id";activity.pendingText="ARCHIVED-NOT-REPLAYED";activity.resolveDelivery(missing);});
            record("unknown_receipt_stays_uncertain_without_automatic_resend",read(()->activity.pendingId.equals("unknown-pending-id")&&!activity.sending&&activity.receiptStatePath().contains("request_id=unknown-pending-id")));
            runOnMainSync(()->{activity.input.setText(activity.pendingText);activity.archivePending("QA unknown receipt");activity.archivedDelivery=new JSONObject();activity.restoreCache();});
            record("archive_unlocks_chat_preserving_encrypted_id_and_text_without_send",read(()->activity.pendingId.isEmpty()&&activity.input.length()==0&&activity.sendButton.getText().toString().equals("Invia")&&activity.archivedDelivery.optString("request_id").equals("unknown-pending-id")&&activity.archivedDelivery.optString("text").equals("ARCHIVED-NOT-REPLAYED")&&!activity.sending&&!activity.uiPrefs.getString("data","").contains("ARCHIVED-NOT-REPLAYED")));
            runOnMainSync(()->activity.tradingHeader.performClick());idle();
            record("trading_panel_expands",read(()->activity.tradingBody.getVisibility()==View.VISIBLE));
            runOnMainSync(()->activity.tradingHeader.performClick());idle();
            record("initial_history_opens_at_bottom",read(()->!activity.scroll.canScrollVertically(1)&&activity.scroll.getScrollY()>0));
            runOnMainSync(()->{for(int i=0;i<15;i++)activity.append("STREAM-"+i+" additional output\n");});idle();
            record("stream_follows_bottom",read(()->!activity.scroll.canScrollVertically(1)));
            runOnMainSync(()->activity.scroll.scrollTo(0,activity.feed.getHeight()/3));idle();
            final int anchor=read(()->activity.scroll.anchor);
            runOnMainSync(()->activity.append("MORE-OUTPUT while reading previous messages\n"));idle();
            record("append_preserves_reading_anchor",read(()->activity.scroll.anchor==anchor&&!activity.scroll.followsTail()));
            record("latest_button_visible_when_reading",read(()->activity.latestButton.getVisibility()==View.VISIBLE));
            waitFor(()->read(()->activity.hasWindowFocus()));
            runOnMainSync(()->{
                activity.input.setText("DRAFT-PRESERVED");activity.input.requestFocus();
                activity.input.post(()->((android.view.inputmethod.InputMethodManager)activity.getSystemService(Context.INPUT_METHOD_SERVICE)).showSoftInput(activity.input,0));
            });
            waitFor(()->read(()->activity.getWindow().getDecorView().getRootWindowInsets().isVisible(WindowInsets.Type.ime())));idle();
            record("keyboard_really_opened",true);
            record("keyboard_preserves_reading_anchor",read(()->activity.scroll.anchor==anchor));
            runOnMainSync(()->((android.view.inputmethod.InputMethodManager)activity.getSystemService(Context.INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(activity.input.getWindowToken(),0));idle();
            final String transcript=read(()->activity.feed.getText().toString());
            runOnMainSync(()->activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE));
            waitFor(()->read(()->activity.getResources().getConfiguration().orientation==Configuration.ORIENTATION_LANDSCAPE));idle();
            record("rotation_keeps_activity_and_chat",read(()->!activity.destroyed&&activity.chat.getVisibility()==View.VISIBLE&&activity.feed.getText().toString().equals(transcript)));
            record("landscape_chat_has_readable_height",read(()->activity.scroll.getHeight()>=activity.dp(32)));
            record("landscape_preserves_reading_anchor",read(()->Math.abs(activity.scroll.anchor-anchor)<130));
            record("rotation_preserves_draft",read(()->activity.input.getText().toString().equals("DRAFT-PRESERVED")));
            runOnMainSync(()->activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT));
            waitFor(()->read(()->activity.getResources().getConfiguration().orientation==Configuration.ORIENTATION_PORTRAIT));idle();
            record("portrait_restores_reading_anchor",read(()->Math.abs(activity.scroll.anchor-anchor)<130));
            runOnMainSync(()->activity.latestButton.performClick());idle();
            record("latest_button_returns_to_bottom",read(()->!activity.scroll.canScrollVertically(1)&&activity.latestButton.getVisibility()==View.GONE));
            final int restoredAnchor=read(()->{activity.scroll.scrollTo(0,activity.feed.getHeight()/2);return activity.scroll.anchor;});idle();
            ActivityMonitor monitor=addMonitor(MainActivity.class.getName(),null,false);
            MainActivity previous=activity;
            runOnMainSync(()->previous.recreate());
            activity=(MainActivity)monitor.waitForActivityWithTimeout(7000);removeMonitor(monitor);
            if(activity==null)throw new AssertionError("Activity recreation missing");idle();
            record("recreation_requires_verified_identity_before_cache_is_shown",read(()->!activity.accountAuthenticated&&activity.chat.getVisibility()==View.GONE));
            runOnMainSync(()->{try{activity.unlockAccount(new JSONObject().put("authenticated",true).put("identity",new JSONObject().put("name","UI fixture")));activity.running=false;activity.generation++;}catch(Exception e){throw new RuntimeException(e);}});idle();
            record("recreation_preserves_chat_and_stream",read(()->activity.feed.getText().toString().equals(transcript)&&activity.chat.getVisibility()==View.VISIBLE));
            record("recreation_preserves_draft",read(()->activity.input.getText().toString().equals("DRAFT-PRESERVED")));
            record("recreation_preserves_reading_anchor",read(()->Math.abs(activity.scroll.anchor-restoredAnchor)<130));
            record("text_remains_selectable",read(()->activity.feed.isTextSelectable()));
            runOnMainSync(()->{activity.pendingId="old-pc-id";activity.pendingText="DO-NOT-REPLAY-TO-NEW-PC";activity.resetConversation();});
            record("new_pc_pairing_cannot_replay_previous_pc_intent",read(()->activity.pendingId.isEmpty()&&activity.pendingText.isEmpty()&&activity.lastRequestId.isEmpty()&&activity.session.isEmpty()&&activity.input.length()==0&&activity.tradingState.length()==0));
            result.put("passed",true);code=Activity.RESULT_OK;
        }catch(Throwable error){
            try{StringWriter stack=new StringWriter();error.printStackTrace(new PrintWriter(stack));result.put("stack",stack.toString());result.put("focus",read(()->new JSONObject().put("window",activity.hasWindowFocus()).put("input",activity.input.hasFocus()).put("gate",activity.accountGate.isShown())));}catch(Exception ignored){}
            try{result.put("passed",false).put("error",error.toString());if(activity!=null)result.put("layout",read(()->new JSONObject().put("root",activity.root.getHeight()).put("chat",activity.chat.getHeight()).put("transcript",activity.scroll.getHeight()).put("toolbar",activity.toolbar.getHeight()).put("input",activity.input.getHeight()).put("sendbar",activity.sendBar.getHeight()).put("status",activity.status.getHeight()).put("assistant",activity.assistantStatus.getHeight()).put("finance",activity.financeStatus.getHeight()).put("density",activity.getResources().getDisplayMetrics().density)));}catch(Exception ignored){}
        }finally{
            output.putString("results",result.toString());
            if(activity!=null)runOnMainSync(()->activity.finish());
            finish(code,output);
        }
    }
}
