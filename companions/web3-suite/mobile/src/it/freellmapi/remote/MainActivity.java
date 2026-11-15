package it.freellmapi.remote;

import android.app.*;
import android.os.*;
import android.content.*;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.security.keystore.*;
import android.util.Base64;
import android.view.*;
import android.view.inputmethod.InputMethodManager;
import android.widget.*;
import org.json.*;
import javax.net.ssl.*;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.*;
import java.util.*;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    LinearLayout accountGate;TextView accountMessage;Button accountLogin;
    volatile boolean accountAuthenticated=false;boolean accountChecking=false;JSONObject accountIdentity=new JSONObject();
    AlertDialog sessionPicker;
    Button hyperliquidButton;
    final int BG=Color.rgb(12,18,32), PANEL=Color.rgb(22,32,50), FG=Color.rgb(226,235,250), CYAN=Color.rgb(100,220,255);
    LinearLayout root,connect,chat,questionBox,toolbar,tools,sessionsBar,composer,brandBar; boolean imeVisible=false; TextView title,status,project,feed; EditText link,urlField,fpField,codeField,input;
    Button modelButton,modeButton,sendButton,pairButton,resumeButton,latestButton; TranscriptScrollView scroll; ScrollView connectScroll,questionScroll; String base="",fingerprint="",token="",session="",questionId=""; final Object connectionLock=new Object();
    JSONArray models=new JSONArray(),sessions=new JSONArray(); volatile long cursor=0; volatile boolean transcriptLoaded=false;
    volatile boolean running=false,destroyed=false; volatile int generation=0;
    ExecutorService network=Executors.newFixedThreadPool(2),control=Executors.newSingleThreadExecutor(),polling=Executors.newSingleThreadExecutor();
    android.content.SharedPreferences prefs;
    LinearLayout tradingPanel,tradingBody; TextView tradingHeader,tradingDetails; boolean tradingExpanded=false;
    JSONObject tradingState=new JSONObject();
    JSONObject serviceState,latestState=new JSONObject();
    TextView assistantStatus,financeStatus,deliveryStatus; LinearLayout tradingCards,sendBar; Dialog detailsDialog;
    Button stopEntriesButton; boolean sending=false,connected=false; String pendingId="",pendingText="",bridgeInstance="",lastRequestId="",lastDeliveryLabel="";
    JSONObject archivedDelivery=new JSONObject();
    android.content.SharedPreferences uiPrefs;
    final Handler cacheHandler=new Handler(Looper.getMainLooper());final Runnable cacheWrite=()->{if(!destroyed)saveCache();};
    int dp(int n){return (int)(getResources().getDisplayMetrics().density*n+.5f);}
    TextView text(String value,int size){TextView t=new TextView(this);t.setText(value);t.setTextColor(FG);t.setTextSize(size);t.setPadding(dp(8),dp(6),dp(8),dp(6));return t;}
    EditText edit(String hint){EditText e=new EditText(this);e.setTextColor(FG);e.setHintTextColor(0xff8294ad);e.setHint(hint);e.setTextSize(14);e.setPadding(dp(12),dp(10),dp(12),dp(10));e.setBackgroundTintList(ColorStateList.valueOf(CYAN));return e;}
    Button button(String label,Runnable action){Button b=new Button(this);b.setText(label);b.setTextSize(12);b.setTextColor(FG);b.setAllCaps(false);b.setBackgroundTintList(ColorStateList.valueOf(PANEL));b.setOnClickListener(v->action.run());return b;}
    void ui(Runnable task){runOnUiThread(()->{if(!destroyed)task.run();});}
    void notice(String value){ui(()->status.setText(value));}
    @Override public void onCreate(Bundle state){
        super.onCreate(state);prefs=getSharedPreferences("remote",MODE_PRIVATE);uiPrefs=getSharedPreferences("chat-cache",MODE_PRIVATE);
        getWindow().setStatusBarColor(BG);getWindow().setNavigationBarColor(BG);
        root=new LinearLayout(this);root.setOrientation(1);root.setBackgroundColor(BG);root.setPadding(dp(12),dp(10),dp(12),dp(8));root.setFitsSystemWindows(true);setContentView(root);
        brandBar=new LinearLayout(this);brandBar.setGravity(Gravity.CENTER_VERTICAL);root.addView(brandBar); ImageView logo=new ImageView(this);logo.setImageResource(getResources().getIdentifier("icon_art","drawable",getPackageName()));logo.setContentDescription("FreeLLMAPI");brandBar.addView(logo,new LinearLayout.LayoutParams(dp(38),dp(38))); title=text("freellmapi Remote",25);title.setTextColor(CYAN);title.setTypeface(null,Typeface.BOLD);brandBar.addView(title);
        status=text("Collega il tuo PC · VPN privata oppure HTTPS verificato",12);root.addView(status);
        connectScroll=new ScrollView(this);connect=new LinearLayout(this);connect.setOrientation(1);connectScroll.addView(connect);root.addView(connectScroll,new LinearLayout.LayoutParams(-1,0,1));
        connect.addView(text("Dal CMD: /remote start, poi /remote pair. Scansiona il QR con la fotocamera del telefono oppure incolla il link.",14));
        link=edit("Link completo oppure URL del PC");link.setMaxLines(3);connect.addView(link);
        urlField=edit("Oppure URL PC mostrato da /remote pair");urlField.setSingleLine(true);connect.addView(urlField);
        fpField=edit("Solo HTTPS: fingerprint SHA-256 mostrato sul PC");fpField.setMaxLines(2);connect.addView(fpField);
        codeField=edit("Codice di abbinamento (8 cifre)");codeField.setInputType(2);connect.addView(codeField);
        pairButton=button("Collega al PC",()->pair());connect.addView(pairButton);
        resumeButton=button("Riprendi collegamento salvato",()->{running=false;generation++;load();if(!token.isEmpty())resumeAccount(false);else notice("Nessun abbinamento salvato: usa un nuovo codice dal PC.");});connect.addView(resumeButton);resumeButton.setVisibility(prefs.contains("credential")?View.VISIBLE:View.GONE);
        connect.addView(button("Apri Tailscale · accesso Google",()->openTailscale()));
        accountGate=new LinearLayout(this);accountGate.setOrientation(1);accountGate.setGravity(Gravity.CENTER);root.addView(accountGate,new LinearLayout.LayoutParams(-1,0,1));accountGate.setVisibility(View.GONE);
        accountGate.addView(text("Accedi con Tailscale / Google",22));accountGate.addView(text("Usa sul Seeker lo stesso account Tailscale del PC. Abbinamento, sessioni e contesto restano conservati.",14));
        accountLogin=button("Accedi con Tailscale / Google",()->resumeAccount(true));accountGate.addView(accountLogin);
        accountGate.addView(button("Apri Tailscale",()->openTailscale()));accountMessage=text("",13);accountGate.addView(accountMessage);
        accountGate.addView(button("Collegamento al PC",()->{showAccountGate("");accountGate.setVisibility(View.GONE);connectScroll.setVisibility(View.VISIBLE);}));
        chat=new LinearLayout(this);chat.setOrientation(1);root.addView(chat,new LinearLayout.LayoutParams(-1,0,1));chat.setVisibility(View.GONE);
        assistantStatus=text("Assistente · stato in attesa del PC",12);chat.addView(assistantStatus);
        financeStatus=text("Trading · stato non verificato",12);financeStatus.setTextColor(CYAN);financeStatus.setOnClickListener(v->showDetails());chat.addView(financeStatus);
        project=text("",12);chat.addView(project);
        toolbar=new LinearLayout(this);toolbar.setOrientation(1);chat.addView(toolbar);
        tools=new LinearLayout(this);toolbar.addView(tools);
        modelButton=button("Modello",()->chooseModel());tools.addView(modelButton,new LinearLayout.LayoutParams(0,dp(48),1));
        modeButton=button("ask",()->new AlertDialog.Builder(this).setTitle("Permessi della sessione").setItems(new String[]{"ask · conferma le azioni","plan · sola pianificazione","auto · autorizza gli strumenti"},(d,w)->{
            String command=new String[]{"/ask","/plan","/auto on"}[w];
            if(w==2)new AlertDialog.Builder(this).setMessage("Attivare l'esecuzione automatica degli strumenti nella sessione PC? Le autorizzazioni finanziarie restano separate.").setPositiveButton("Attiva",(a,b)->send(command)).setNegativeButton("Annulla",null).show();else send(command);
        }).show());tools.addView(modeButton);
        sessionsBar=new LinearLayout(this);toolbar.addView(sessionsBar);
        hyperliquidButton=button("Hyperliquid",()->send("/hyperliquid"));sessionsBar.addView(hyperliquidButton,new LinearLayout.LayoutParams(0,dp(46),1));
        sessionsBar.addView(button("Sessioni",()->chooseSession()),new LinearLayout.LayoutParams(0,dp(46),1));
        sessionsBar.addView(button("Dettagli / altro",()->showDetails()),new LinearLayout.LayoutParams(0,dp(46),1));
        tools.addView(button("Account",()->showAccount()));
        tradingPanel=new LinearLayout(this);tradingPanel.setOrientation(1);tradingPanel.setBackgroundColor(PANEL);chat.addView(tradingPanel);
        tradingHeader=text("Hyperliquid · stato conto",13);tradingHeader.setTextColor(CYAN);tradingHeader.setContentDescription("Mostra o nascondi il pannello Hyperliquid");tradingPanel.addView(tradingHeader);
        tradingBody=new LinearLayout(this);tradingBody.setOrientation(1);tradingBody.setVisibility(View.GONE);tradingPanel.addView(tradingBody);
        tradingHeader.setOnClickListener(v->{tradingExpanded=!tradingExpanded;tradingBody.setVisibility(tradingExpanded?View.VISIBLE:View.GONE);});
        ScrollView tradingScroll=new ScrollView(this);tradingCards=new LinearLayout(this);tradingCards.setOrientation(1);tradingScroll.addView(tradingCards);tradingDetails=text("Dati non ancora verificati",12);tradingBody.addView(tradingScroll,new LinearLayout.LayoutParams(-1,dp(150)));
        LinearLayout tradingActions=new LinearLayout(this);tradingBody.addView(tradingActions);
        tradingActions.addView(button("Analizza",()->chooseMarket(false)),new LinearLayout.LayoutParams(0,dp(48),1));
        tradingActions.addView(button("Prepara ingresso",()->chooseMarket(true)),new LinearLayout.LayoutParams(0,dp(48),1));
        stopEntriesButton=button("Ferma nuovi ingressi",()->stopEntries());tradingActions.addView(stopEntriesButton,new LinearLayout.LayoutParams(0,dp(48),1));
        feed=text("",13);feed.setTypeface(Typeface.MONOSPACE);feed.setTextIsSelectable(true);scroll=new TranscriptScrollView(this,feed);chat.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        latestButton=button("↓ Ultimi messaggi",()->scroll.latest());latestButton.setVisibility(View.GONE);chat.addView(latestButton);scroll.followChanged=()->latestButton.setVisibility(scroll.followsTail()?View.GONE:View.VISIBLE);
        questionScroll=new ScrollView(this);questionBox=new LinearLayout(this);questionBox.setOrientation(1);questionBox.setBackgroundColor(PANEL);questionScroll.addView(questionBox);chat.addView(questionScroll,new LinearLayout.LayoutParams(-1,dp(240)));questionScroll.setVisibility(View.GONE);
        composer=new LinearLayout(this);composer.setOrientation(1);chat.addView(composer);
        input=edit("Scrivi a freellmapi sul PC…");input.setMinLines(1);input.setMaxLines(4);input.setInputType(131073);input.setImeOptions(android.view.inputmethod.EditorInfo.IME_FLAG_NO_EXTRACT_UI);composer.addView(input);
        input.addTextChangedListener(new android.text.TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){}public void onTextChanged(CharSequence s,int start,int before,int count){}public void afterTextChanged(android.text.Editable e){cacheHandler.removeCallbacks(cacheWrite);cacheHandler.postDelayed(cacheWrite,250);}});
        deliveryStatus=text("",11);deliveryStatus.setVisibility(View.GONE);composer.addView(deliveryStatus);
        sendBar=new LinearLayout(this);composer.addView(sendBar);
        sendButton=button("Invia",()->{String value=input.getText().toString().trim();if(!value.isEmpty()){scroll.latest();send(value);}});sendBar.addView(sendButton,new LinearLayout.LayoutParams(0,dp(48),1));
        sendBar.addView(button("Ferma AI",()->controlPost("/v1/cancel",result->notice(result.optBoolean("requested")?"Interruzione AI richiesta · il monitor trading è separato":"Nessuna risposta AI in esecuzione"))));
        adjustLayout(getResources().getConfiguration());
        root.setOnApplyWindowInsetsListener((view,insets)->{
            boolean visible=insets.isVisible(WindowInsets.Type.ime());
            if(visible!=imeVisible){imeVisible=visible;adjustLayout(getResources().getConfiguration());}
            return view.onApplyWindowInsets(insets);
        });
        load();
        if(state!=null&&accountAuthenticated&&!token.isEmpty()){
            session=state.getString("chat.session","");cursor=state.getLong("chat.cursor");transcriptLoaded=state.getBoolean("chat.loaded");
            scroll.replaceText(state.getString("chat.text",""),true);scroll.loadViewport(state.getBundle("chat.viewport"));input.setText(state.getString("chat.draft",""));
        }
        acceptLink(getIntent());
    }
    @Override protected void onStart(){super.onStart();if(!token.isEmpty())resumeAccount(false);}
    @Override protected void onStop(){cacheHandler.removeCallbacks(cacheWrite);saveCache();running=false;generation++;super.onStop();}
    @Override protected void onDestroy(){destroyed=true;cacheHandler.removeCallbacks(cacheWrite);running=false;generation++;network.shutdownNow();control.shutdownNow();polling.shutdownNow();if(detailsDialog!=null)detailsDialog.dismiss();if(sessionPicker!=null)sessionPicker.dismiss();super.onDestroy();}
    @Override public void onConfigurationChanged(Configuration config){super.onConfigurationChanged(config);adjustLayout(config);}
    void adjustLayout(Configuration config){
        boolean landscape=config.orientation==Configuration.ORIENTATION_LANDSCAPE;
        brandBar.setVisibility(landscape?View.GONE:View.VISIBLE);
        boolean compact=landscape&&imeVisible;
        status.setVisibility(compact?View.GONE:View.VISIBLE);assistantStatus.setVisibility(compact?View.GONE:View.VISIBLE);
        financeStatus.setVisibility(compact?View.GONE:View.VISIBLE);toolbar.setVisibility(compact?View.GONE:View.VISIBLE);
        title.setVisibility(landscape?View.GONE:View.VISIBLE);status.setMaxLines(landscape?1:2);project.setVisibility(landscape?View.GONE:View.VISIBLE);
        assistantStatus.setMaxLines(landscape?1:2);financeStatus.setMaxLines(1);
        for(TextView t:new TextView[]{status,assistantStatus,financeStatus})t.setPadding(dp(8),dp(landscape?1:6),dp(8),dp(landscape?1:6));
        tradingHeader.setVisibility(landscape?View.GONE:View.VISIBLE);deliveryStatus.setVisibility(landscape||deliveryStatus.length()==0?View.GONE:View.VISIBLE);
        if(landscape){tradingExpanded=false;tradingBody.setVisibility(View.GONE);}
        toolbar.setOrientation(landscape?0:1);
        tools.setLayoutParams(new LinearLayout.LayoutParams(landscape?0:-1,-2,landscape?1:0));
        sessionsBar.setLayoutParams(new LinearLayout.LayoutParams(landscape?0:-1,-2,landscape?1:0));
        input.setMaxLines(landscape?2:4);latestButton.setLayoutParams(new LinearLayout.LayoutParams(-1,dp(landscape?28:36)));
        composer.setOrientation(landscape?0:1);
        input.setLayoutParams(new LinearLayout.LayoutParams(landscape?0:-1,-2,landscape?1:0));
        sendBar.setLayoutParams(new LinearLayout.LayoutParams(landscape?dp(160):-1,-2));
        questionScroll.setLayoutParams(new LinearLayout.LayoutParams(-1,dp(landscape?96:240)));
    }
    @Override protected void onSaveInstanceState(Bundle state){
        state.putString("chat.text",feed.getText().toString());state.putBundle("chat.viewport",scroll.saveViewport());state.putString("chat.draft",input.getText().toString());
        state.putString("chat.session",session);state.putLong("chat.cursor",cursor);state.putBoolean("chat.loaded",transcriptLoaded);
        super.onSaveInstanceState(state);
    }
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);acceptLink(intent);}
    void openTailscale(){
        try{
            Intent intent=getPackageManager().getLaunchIntentForPackage("com.tailscale.ipn");
            if(intent!=null){startActivity(intent);return;}
        }catch(ActivityNotFoundException|SecurityException e){
            notice("Android non permette di aprire Tailscale: verifica che sia abilitata nello stesso profilo.");
        }
        new AlertDialog.Builder(this).setTitle("Tailscale non disponibile")
            .setMessage("Non trovo Tailscale apribile in questo profilo Android. Se è già installata, aprila dal telefono e verifica che sia abilitata. Puoi anche aprire la pagina ufficiale di download.")
            .setPositiveButton("Apri pagina download",(d,w)->{
                try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse("https://tailscale.com/download/android")));}
                catch(ActivityNotFoundException|SecurityException e){notice("Nessun browser disponibile per aprire tailscale.com/download/android.");}
            }).setNegativeButton("Annulla",null).show();
    }
    void showAccountGate(String message){
        saveCache();running=false;generation++;accountAuthenticated=false;cacheHandler.removeCallbacks(cacheWrite);
        connectScroll.setVisibility(View.GONE);chat.setVisibility(View.GONE);accountGate.setVisibility(View.VISIBLE);accountMessage.setText(message);
        if(detailsDialog!=null)detailsDialog.dismiss();if(sessionPicker!=null)sessionPicker.dismiss();feed.setText("");input.setText("");questionBox.removeAllViews();questionId="";
        latestState=new JSONObject();tradingState=new JSONObject();serviceState=null;models=new JSONArray();sessions=new JSONArray();accountIdentity=new JSONObject();
        project.setText("");assistantStatus.setText("");financeStatus.setText("");tradingCards.removeAllViews();tradingDetails.setText("");delivery("");
        status.setText("Account · sessioni e abbinamento conservati");
    }
    void unlockAccount(JSONObject value){
        if(!value.optBoolean("authenticated")){showAccountGate(value.optString("message","Premi Accedi con Tailscale / Google."));return;}
        accountAuthenticated=true;accountIdentity=value.optJSONObject("identity");if(accountIdentity==null)accountIdentity=new JSONObject();
        prefs.edit().putBoolean("account_signed_out",false).commit();restoreCache();
        connectScroll.setVisibility(View.GONE);accountGate.setVisibility(View.GONE);chat.setVisibility(View.VISIBLE);startPolling();
    }
    void resumeAccount(boolean login){
        if(token.isEmpty()||accountChecking)return;
        if(!login&&prefs.getBoolean("account_signed_out",false)){showAccountGate("Uscita effettuata. Accedi per riprendere il collegamento salvato.");return;}
        accountChecking=true;accountLogin.setEnabled(false);accountMessage.setText("Verifica account Tailscale sul PC…");final int mine=generation;
        control.execute(()->{try{JSONObject value=request(login?"/v1/account/login":"/v1/account/status",login?new JSONObject():null);ui(()->{if(generation==mine)unlockAccount(value);});}
            catch(Exception e){ui(()->{if(generation==mine)showAccountGate(e instanceof AccountRequiredException||e instanceof IOException?e.getMessage():"Verifica account non riuscita. Abbinamento conservato.");});}
            finally{ui(()->{accountChecking=false;accountLogin.setEnabled(true);});}});
    }
    void logoutAccount(){
        saveCache();prefs.edit().putBoolean("account_signed_out",true).commit();showAccountGate("Uscita da FREELLMAPI. Tailscale, sessioni e collegamento del PC restano attivi.");
        control.execute(()->{try{request("/v1/account/logout",new JSONObject());}catch(Exception e){ui(()->accountMessage.setText("Uscita locale completata; conferma dal PC non disponibile. Abbinamento e contesto conservati."));}});
    }
    void showAccount(){
        if(!accountAuthenticated){showAccountGate("Accedi con il tuo account Tailscale / Google.");return;}
        new AlertDialog.Builder(this).setTitle("Account Tailscale / Google").setMessage(accountIdentity.optString("name")+"\n"+accountIdentity.optString("login")+"\n\nEsci solo da FREELLMAPI: Tailscale e il coordinatore del PC restano attivi.")
            .setPositiveButton("Esci da FREELLMAPI",(d,w)->logoutAccount()).setNeutralButton("Apri Tailscale",(d,w)->openTailscale()).setNegativeButton("Chiudi",null).show();
    }
    void switchCoordinator(JSONObject destination,String targetSession){
        try{
            URI previous=new URI(base),next=new URI(destination.getString("url"));
            if(!targetSession.matches("[a-f0-9]{32}")||!Objects.equals(previous.getHost(),next.getHost())||!Objects.equals(previous.getScheme(),next.getScheme())||next.getUserInfo()!=null||next.getQuery()!=null||next.getFragment()!=null||!(next.getPath().isEmpty()||next.getPath().equals("/")))throw new IOException("Destinazione non valida");
            String endpoint=destination.getString("url"),pin=destination.optString("fingerprint"),instance=destination.getString("instance");
            if("https".equals(next.getScheme())&&!fingerprint.equals(pin))throw new IOException("Fingerprint differente");
            saveCache();running=false;final int mine=++generation;notice("Collegamento al coordinatore della conversazione…");
            network.execute(()->{try{
                JSONObject state=requestTo(endpoint,pin,token,"/v1/state",null);
                if(!targetSession.equals(state.optString("session"))||!instance.equals(state.optString("instance")))throw new IOException("Sessione del coordinatore cambiata");
                synchronized(connectionLock){if(generation!=mine)return;save(endpoint,pin,token);base=endpoint;fingerprint=pin;}
                ui(()->{if(generation!=mine)return;cursor=state.optLong("cursor");bridgeInstance=instance;transcriptLoaded=false;render(state,true);session=targetSession;transcriptLoaded=true;delivery("Conversazione ripresa · nessun messaggio reinviato");saveCache();startPolling();notice("Sessione condivisa ripresa · nessun secondo coordinatore o vecchia azione rieseguita");});
            }catch(Exception e){ui(()->{if(generation!=mine)return;delivery("Cambio conversazione non completato · nessun messaggio reinviato");notice("Passaggio non completato; collegamento precedente conservato. Nessuna azione reinviata.");startPolling();});}});
        }catch(Exception e){notice("Coordinatore non verificato. Nessuna nuova azione o esecuzione.");startPolling();}
    }
    void acceptLink(Intent intent){
        Uri value=intent.getData();if(value==null||!"freellmapi".equals(value.getScheme())||!"pair".equals(value.getHost()))return;
        String incoming=value.getQueryParameter("url"),pin=value.getQueryParameter("fp");
        if(!token.isEmpty()&&base.equals(incoming)&&fingerprint.equals(pin==null?"":pin)){
            resumeAccount(false);notice("Riprendo il collegamento salvato · nessun nuovo codice necessario");return;
        }
        running=false;generation++;connectScroll.setVisibility(View.VISIBLE);chat.setVisibility(View.GONE);link.setText(value.toString());notice("Controlla che il link provenga dal tuo PC, poi premi Collega.");
    }
    void append(String value){scroll.appendText(value);}
    void saveCache(){
        if(!accountAuthenticated||token.isEmpty()||session.isEmpty())return;
        try{
            Bundle viewport=scroll.saveViewport();JSONObject data=new JSONObject().put("base",base).put("fp",fingerprint).put("session",session).put("cursor",cursor).put("instance",bridgeInstance)
                .put("text",feed.getText().toString()).put("draft",input.getText().toString()).put("expanded",tradingExpanded)
                .put("tail",viewport.getBoolean("tail")).put("anchor",viewport.getInt("anchor")).put("offset",viewport.getInt("offset"))
                .put("pending_id",pendingId).put("pending_text",pendingText).put("last_request_id",lastRequestId).put("archived_delivery",archivedDelivery);
            Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key());
            if(!uiPrefs.edit().putString("iv",Base64.encodeToString(c.getIV(),Base64.NO_WRAP)).putString("data",Base64.encodeToString(c.doFinal(data.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP)).commit())throw new IOException("Cache storage unavailable");
        }catch(Exception ignored){notice("Bozza in memoria · salvataggio locale non confermato");}
    }
    void restoreCache(){
        if(!accountAuthenticated)return;
        try{
            if(!uiPrefs.contains("data"))return;Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(uiPrefs.getString("iv",""),Base64.NO_WRAP)));
            JSONObject data=new JSONObject(new String(c.doFinal(Base64.decode(uiPrefs.getString("data",""),Base64.NO_WRAP)),StandardCharsets.UTF_8));
            if(!base.equals(data.optString("base"))||!fingerprint.equals(data.optString("fp")))return;
            session=data.optString("session");cursor=data.optLong("cursor");bridgeInstance=data.optString("instance");transcriptLoaded=true;
            scroll.replaceText(data.optString("text"),false);Bundle viewport=new Bundle();viewport.putBoolean("tail",data.optBoolean("tail",true));viewport.putInt("anchor",data.optInt("anchor"));viewport.putInt("offset",data.optInt("offset"));scroll.loadViewport(viewport);
            input.setText(data.optString("draft"));tradingExpanded=data.optBoolean("expanded")&&getResources().getConfiguration().orientation!=Configuration.ORIENTATION_LANDSCAPE;tradingBody.setVisibility(tradingExpanded?View.VISIBLE:View.GONE);
            pendingId=data.optString("pending_id");pendingText=data.optString("pending_text");
            lastRequestId=data.optString("last_request_id");
            JSONObject archived=data.optJSONObject("archived_delivery");archivedDelivery=archived==null?new JSONObject():archived;
            if(!pendingId.isEmpty()){delivery("Esito dell'ultimo invio da verificare · nessun reinvio automatico");sendButton.setText("Verifica invio");}
        }catch(Exception ignored){/* Server history remains canonical; cached UI is optional. */}
    }
    void delivery(String message){deliveryStatus.setText(message);deliveryStatus.setVisibility(message.isEmpty()?View.GONE:View.VISIBLE);}
    void controlPost(String path,Done done){
        final int mine=generation;control.execute(()->{try{JSONObject result=request(path,new JSONObject());ui(()->{if(generation==mine)done.accept(result);});}
            catch(Exception e){ui(()->{if(generation==mine)notice("Comando non confermato · "+connectionProblem(e)+" · verifica lo stato, nessun reinvio automatico");});}
            finally{if(path.equals("/v1/trading/service-stop"))ui(()->stopEntriesButton.setEnabled(true));}});
    }
    void stopEntries(){
        stopEntriesButton.setEnabled(false);notice("Arresto nuovi ingressi in attesa di conferma dal PC…");
        controlPost("/v1/trading/service-stop",r->{stopEntriesButton.setEnabled(true);
            notice(r.optBoolean("new_orders_stopped")?"Arresto confermato dal PC · non chiude posizioni e non cancella gli ordini già aperti":"Arresto non confermato · verifica lo stato sul PC");});
    }
    void chooseMarket(boolean conditional){
        LinearLayout box=new LinearLayout(this);box.setOrientation(1);EditText market=edit("Mercato: es. BTC, PUMP o dex:COIN");market.setSingleLine(true);box.addView(market);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle(conditional?"Prepara ingresso condizionato":"Analizza un mercato").setView(box).setPositiveButton("Prepara richiesta",null).setNegativeButton("Annulla",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String coin=market.getText().toString().trim();if(!coin.matches("[A-Za-z0-9_:.\\-/]{1,40}")){market.setError("Indica un solo mercato");return;}
            String request=conditional?"Prepara un piano di ingresso condizionato per "+coin+" su Hyperliquid: dati aggiornati, livello futuro, conferma, scadenza, entry, TP, SL e scenario contrario. Rispetta i limiti salvati. Solo preparazione: non armare il monitor e non inviare ordini. Chiedi solo le scelte mancanti, una alla volta con risposta libera alternativa.":"Analizza "+coin+" su Hyperliquid con dati aggiornati. Solo analisi: non inviare ordini, non modificare leva o posizioni.";
            input.setText(request);input.requestFocus();cacheHandler.removeCallbacks(cacheWrite);saveCache();delivery("Richiesta preparata · premi Invia per mandarla al PC");dialog.dismiss();}));dialog.show();
    }
    void card(LinearLayout parent,String heading,String body){
        LinearLayout box=new LinearLayout(this);box.setOrientation(1);box.setPadding(dp(6),dp(4),dp(6),dp(6));box.setBackgroundColor(0xff111e31);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2);params.setMargins(dp(4),dp(4),dp(4),dp(4));parent.addView(box,params);
        TextView h=text(heading,13);h.setTextColor(CYAN);h.setTypeface(null,Typeface.BOLD);box.addView(h);TextView detail=text(body,12);detail.setTextIsSelectable(true);box.addView(detail);
    }
    void fillCards(LinearLayout parent){fillCards(parent,false);}
    void fillCards(LinearLayout parent,boolean detailed){
        parent.removeAllViews();
        JSONArray positions=tradingState.optJSONArray("positions");
        if(positions!=null&&positions.length()>0)for(int i=0;i<Math.min(detailed?positions.length():3,positions.length());i++){JSONObject p=positions.optJSONObject(i);if(p!=null)card(parent,"Posizione osservata · "+tradingState.optString("network"),TradingPresentation.position(p,tradingState));}
        else card(parent,"Posizioni osservate",positions==null?"Lettura non disponibile · nessun saldo o chiusura dedotti":"ready".equals(tradingState.optString("state"))?"Nessuna posizione nella lettura corrente":"Dati precedenti · aggiorna per verificare");
        if(serviceState!=null){
            card(parent,(connected?"":"ULTIMO STATO · ")+TradingPresentation.service(serviceState),"/auto autorizza gli strumenti, non il trading.\n"+
                "Heartbeat: "+TradingPresentation.value(serviceState,"heartbeat_age_seconds")+"s · "+serviceState.optString("paused_reason","").replace("null",""));
            JSONObject policy=serviceState.optJSONObject("policy");if(detailed&&policy!=null)card(parent,"Limiti del servizio salvati",serviceState.optString("profile","")+" · "+serviceState.optString("network","")+"\nEsposizione massima "+TradingPresentation.value(policy,"max_exposure_usd")+" USDC · per ordine "+TradingPresentation.value(policy,"max_order_usd")+" USDC\nLeva massima "+TradingPresentation.value(policy,"max_leverage")+"× · arresto nuovi invii a calo equity "+TradingPresentation.value(policy,"max_loss_usd")+" USDC\nL'arresto non chiude posizioni e non garantisce una perdita massima.");
            JSONObject monitor=serviceState.optJSONObject("entry_monitor");JSONArray watches=monitor==null?null:monitor.optJSONArray("watches");
            int shown=0;if(watches!=null)for(int i=0;i<watches.length()&&shown<(detailed?12:3);i++){JSONObject w=watches.optJSONObject(i);if(w==null)continue;String phase=w.optString("state");boolean terminal=Arrays.asList("done","cancelled","expired","invalidated","abandoned").contains(phase);if(!detailed&&terminal)continue;card(parent,"Setup condizionato",TradingPresentation.watch(w));shown++;}
            if(shown==0)card(parent,"Setup condizionati","Nessun setup attivo riportato dal monitor. Un piano in chat non è un ordine armato.");
        }else card(parent,"Monitor trading","Stato non disponibile · aggiorna il bridge sul PC.");
    }
    void showDetails(){
        if(detailsDialog!=null)detailsDialog.dismiss();detailsDialog=new Dialog(this);LinearLayout sheet=new LinearLayout(this);sheet.setOrientation(1);sheet.setPadding(dp(12),dp(12),dp(12),dp(12));sheet.setBackgroundColor(BG);
        sheet.addView(text("Dettagli · sessione e trading",18));ScrollView view=new ScrollView(this);LinearLayout body=new LinearLayout(this);body.setOrientation(1);view.addView(body);sheet.addView(view,new LinearLayout.LayoutParams(-1,0,1));
        card(body,"Sessione",latestState.optString("project")+"\nID "+session+"\nAPI "+latestState.optString("api","non verificata")+"\n"+latestState.optString("progress","")+"\n"+deliveryStatus.getText());
        if(!pendingId.isEmpty())card(body,"Invio da verificare","ID richiesta "+pendingId+"\nConservato sul telefono. La verifica cerca l'ID preciso; non reinvia messaggi.");
        if(archivedDelivery.length()>0)card(body,"Ultimo invio archiviato","ID richiesta "+archivedDelivery.optString("request_id")+"\n"+archivedDelivery.optString("reason")+"\nBozza: "+archivedDelivery.optString("text")+"\nNessun reinvio automatico.");
        card(body,"Dati osservati",tradingState.has("retrieved_ms")?"Ultima lettura conto: "+java.text.DateFormat.getDateTimeInstance().format(new java.util.Date(tradingState.optLong("retrieved_ms")))+"\nI dettagli sono una fotografia di quel momento. Aggiorna per rileggere lo stato.":"Nessuna lettura conto verificata.");
        body.addView(button("Aggiorna dati conto",()->post("/v1/trading/refresh",new JSONObject(),r->{renderTrading(r);showDetails();})));
        LinearLayout cards=new LinearLayout(this);cards.setOrientation(1);body.addView(cards);fillCards(cards,true);card(body,"Conto reale osservato · sola lettura",tradingDetails.getText().toString());
        card(body,"Ricevute del gestore ordini",serviceState==null?"Non disponibili":serviceState.optJSONObject("order_manager")==null?"Non disponibili":serviceState.optJSONObject("order_manager").toString());
        body.addView(button("Nuova sessione",()->new AlertDialog.Builder(this).setMessage("La conversazione corrente resta salvata sul PC.").setPositiveButton("Nuova",(d,w)->{detailsDialog.dismiss();send("/new");}).setNegativeButton("Annulla",null).show()));
        body.addView(button("Scollega questo telefono",()->new AlertDialog.Builder(this).setMessage("Rimuovere l'abbinamento locale? Le sessioni sul PC restano salvate.").setPositiveButton("Scollega",(d,w)->{detailsDialog.dismiss();disconnect();}).setNegativeButton("Annulla",null).show()));
        sheet.addView(button("Chiudi dettagli",()->detailsDialog.dismiss()));detailsDialog.setContentView(sheet);Window window=detailsDialog.getWindow();if(window!=null){window.setBackgroundDrawableResource(android.R.color.transparent);window.setGravity(Gravity.BOTTOM);window.setLayout(-1,(int)(getResources().getDisplayMetrics().heightPixels*.82));}detailsDialog.show();if(window!=null)window.setLayout(-1,(int)(getResources().getDisplayMetrics().heightPixels*.82));
    }
    SecretKey key() throws Exception {
        KeyStore ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);
        if(!ks.containsAlias("freellmapi.remote")){KeyGenerator g=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");g.init(new KeyGenParameterSpec.Builder("freellmapi.remote",KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());g.generateKey();}
        return (SecretKey)ks.getKey("freellmapi.remote",null);
    }
    void save(String endpoint,String pin,String credential) throws Exception {
        Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.ENCRYPT_MODE,key());
        if(!prefs.edit().putString("base",endpoint).putString("fp",pin).putString("iv",Base64.encodeToString(c.getIV(),Base64.NO_WRAP)).putString("credential",Base64.encodeToString(c.doFinal(credential.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP)).commit())throw new IOException("Device storage unavailable");
    }
    void load(){try{
        String encrypted=prefs.getString("credential","");if(encrypted.isEmpty())return;
        base=prefs.getString("base","");fingerprint=prefs.getString("fp","");Cipher c=Cipher.getInstance("AES/GCM/NoPadding");c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),Base64.NO_WRAP)));token=new String(c.doFinal(Base64.decode(encrypted,Base64.NO_WRAP)),StandardCharsets.UTF_8);
        connectScroll.setVisibility(View.GONE);chat.setVisibility(View.GONE);accountGate.setVisibility(View.VISIBLE);
    }catch(Exception e){prefs.edit().clear().apply();token="";notice("Abbinamento locale da ricreare: genera un nuovo codice dal PC.");}}
    void resetConversation(){
        session="";cursor=0;bridgeInstance="";transcriptLoaded=false;pendingId="";pendingText="";lastRequestId="";lastDeliveryLabel="";connected=false;archivedDelivery=new JSONObject();
        uiPrefs.edit().clear().apply();input.setText("");delivery("");sendButton.setText("Invia");scroll.replaceText("",true);questionId="";questionBox.removeAllViews();questionScroll.setVisibility(View.GONE);
        serviceState=null;tradingState=new JSONObject();renderTrading(null);financeStatus.setText("Trading · in attesa del nuovo PC");
    }
    void disconnect(){running=false;generation++;synchronized(connectionLock){token="";}transcriptLoaded=false;pendingId="";pendingText="";uiPrefs.edit().clear().apply();prefs.edit().clear().apply();resumeButton.setVisibility(View.GONE);connectScroll.setVisibility(View.VISIBLE);chat.setVisibility(View.GONE);questionBox.removeAllViews();questionId="";notice("Telefono scollegato. Le sessioni restano sul PC. Per revocare tutti i dispositivi: /remote revoke.");}
    void pair(){
        try{
            String raw=link.getText().toString().trim().replace("\\:",":").replace("\\/","/");String nextBase=urlField.getText().toString().trim().replace("\\:",":").replace("\\/","/"),nextFp=fpField.getText().toString().replace(":","").trim(),code=codeField.getText().toString().trim();
            if(raw.startsWith("http://")||raw.startsWith("https://"))nextBase=raw;
            else if(!raw.isEmpty()){Uri uri=Uri.parse(raw);if(!"freellmapi".equals(uri.getScheme())||!"pair".equals(uri.getHost()))throw new Exception();nextBase=uri.getQueryParameter("url");nextFp=uri.getQueryParameter("fp");code=uri.getQueryParameter("code");}
            URI uri=new URI(nextBase);boolean https="https".equals(uri.getScheme());if((!https&&!"http".equals(uri.getScheme()))||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null||!(uri.getPath().isEmpty()||uri.getPath().equals("/"))||(https&&(nextFp==null||!nextFp.matches("[a-fA-F0-9]{64}")))||(!https&&!privateHost(uri.getHost()))||code==null||!code.matches("[0-9]{8}"))throw new Exception();if(nextFp==null)nextFp="";
            running=false;final int pairing=++generation;final String endpoint=nextBase.replaceAll("/$",""),pin=nextFp.toLowerCase(Locale.ROOT);final JSONObject body=new JSONObject().put("code",code).put("name",Build.MANUFACTURER+" "+Build.MODEL);notice("Verifica connessione e abbinamento…");pairButton.setEnabled(false);
            network.execute(()->{
                try{
                    JSONObject result=requestTo(endpoint,pin,"","/v1/pair",body);String credential=result.getString("token");
                    synchronized(connectionLock){if(generation!=pairing)return;save(endpoint,pin,credential);base=endpoint;fingerprint=pin;token=credential;}
                    ui(()->{if(generation!=pairing)return;link.setText("");codeField.setText("");resumeButton.setVisibility(View.VISIBLE);accountAuthenticated=false;connectScroll.setVisibility(View.GONE);chat.setVisibility(View.GONE);resetConversation();showAccountGate("Abbinamento conservato. Premi Accedi per verificare il tuo account Tailscale.");});
                }catch(SecurityException e){if(generation==pairing)notice("Codice già usato o scaduto. Riprendi il collegamento salvato oppure genera /remote pair sul PC.");}
                catch(Exception e){if(generation==pairing)notice("Abbinamento non riuscito · "+connectionProblem(e)+" · Il collegamento salvato è conservato.");}
                finally{ui(()->pairButton.setEnabled(true));}
            });
        }catch(Exception e){notice("Inserisci il link completo oppure URL del PC e codice a 8 cifre. Il fingerprint serve solo con HTTPS.");}
    }
    byte[] hex(String value){byte[] data=new byte[value.length()/2];for(int i=0;i<data.length;i++)data[i]=(byte)Integer.parseInt(value.substring(i*2,i*2+2),16);return data;}
    boolean privateHost(String host){if(host.equals("127.0.0.1"))return true;if(!host.matches("100\\.[0-9]{1,3}\\.[0-9]{1,3}\\.[0-9]{1,3}"))return false;String[] p=host.split("\\.");int second=Integer.parseInt(p[1]),third=Integer.parseInt(p[2]),fourth=Integer.parseInt(p[3]);return second>=64&&second<=127&&third<=255&&fourth<=255&&host.equals("100."+second+"."+third+"."+fourth);}
    JSONObject request(String path,JSONObject body) throws Exception {
        final String endpoint,expected,auth;synchronized(connectionLock){if(!path.startsWith("/v1/account/")&&!accountAuthenticated)throw new AccountRequiredException("Accedi con Tailscale / Google");endpoint=base;expected=fingerprint;auth=token;}return requestTo(endpoint,expected,auth,path,body);
    }
    String connectionProblem(Exception error){if(error instanceof java.net.SocketTimeoutException)return "timeout verso il PC";if(error instanceof javax.net.ssl.SSLException)return "certificato HTTPS non verificato";if(error instanceof java.net.UnknownHostException)return "indirizzo PC non risolto";if("Private VPN must be connected".equals(error.getMessage()))return "Android non segnala la VPN attiva per FREELLMAPI";return error.getClass().getSimpleName();}
    JSONObject requestTo(String endpoint,String expected,String auth,String path,JSONObject body) throws Exception {
        final String host=new URI(endpoint).getHost();
        TrustManager[] trust={new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] chain,String type) throws CertificateException {throw new CertificateException();}
            public void checkServerTrusted(X509Certificate[] chain,String type) throws CertificateException {try{if(chain.length==0||!MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded()),hex(expected)))throw new CertificateException("Certificate pin mismatch");chain[0].checkValidity();}catch(CertificateException e){throw e;}catch(Exception e){throw new CertificateException();}}
        }};
        SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(null,trust,new SecureRandom());
        URL target=new URL(endpoint+path);HttpURLConnection c=(HttpURLConnection)target.openConnection(Proxy.NO_PROXY);
        if(c instanceof HttpsURLConnection){((HttpsURLConnection)c).setSSLSocketFactory(ssl.getSocketFactory());((HttpsURLConnection)c).setHostnameVerifier((name,s)->name.equals(host));}
        else{
            if(!privateHost(host))throw new SecurityException("HTTP requires private tunnel");
            if(!host.equals("127.0.0.1")){ConnectivityManager cm=(ConnectivityManager)getSystemService(CONNECTIVITY_SERVICE);NetworkCapabilities caps=cm.getNetworkCapabilities(cm.getActiveNetwork());if(caps==null||!caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN))throw new IOException("Private VPN must be connected");}
        }
        c.setInstanceFollowRedirects(false);c.setConnectTimeout(7000);c.setReadTimeout(10000);c.setRequestProperty("Accept","application/json");
        if(!auth.isEmpty()){c.setRequestProperty("Authorization","Bearer "+auth);c.setRequestProperty("X-Freellmapi-Account","required");}
        try{
            if(body!=null){c.setRequestMethod("POST");c.setDoOutput(true);c.setRequestProperty("Content-Type","application/json");byte[] raw=body.toString().getBytes(StandardCharsets.UTF_8);c.setFixedLengthStreamingMode(raw.length);try(OutputStream out=c.getOutputStream()){out.write(raw);}}
            int code=c.getResponseCode();if(code==401)throw new SecurityException("Pairing expired");if(code==403)throw new AccountRequiredException("Account Tailscale non verificato. Apri Tailscale e riprova l’accesso.");if(code==400&&"/v1/input".equals(path))throw new InputRejectedException();if(code!=200)throw new IOException(code==404&&path.startsWith("/v1/account/")?"Aggiorna il bridge Remote sul PC: accesso account non disponibile.":"HTTP "+code);
            ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] data=new byte[8192];int n;while((n=in.read(data))!=-1){if(out.size()+n>2500000)throw new IOException("Response too large");out.write(data,0,n);}}
            return new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));
        }finally{c.disconnect();}
    }
    interface Done {void accept(JSONObject data);}
    static class InputRejectedException extends IOException {InputRejectedException(){super("PC rejected input (HTTP 400)");}}
    static class AccountRequiredException extends IOException {AccountRequiredException(String message){super(message);}}
    String receiptStatePath(){String id=pendingId.isEmpty()?lastRequestId:pendingId;return "/v1/state"+(id.isEmpty()?"":"?request_id="+Uri.encode(id));}
    void archivePending(String reason){
        if(pendingId.isEmpty())return;
        try{archivedDelivery=new JSONObject().put("request_id",pendingId).put("text",pendingText).put("session",session).put("reason",reason).put("at_ms",System.currentTimeMillis());}catch(JSONException ignored){return;}
        // Keep the uncertain message in encrypted Details, not queued in the
        // composer where a second tap could submit it with a new ID.
        if(input.getText().toString().trim().equals(pendingText))input.setText("");
        pendingId="";pendingText="";sendButton.setText("Invia");delivery(reason+" · bozza conservata nei Dettagli · nessun reinvio");saveCache();
    }
    void post(String path,JSONObject data,Done done){final int mine=generation;network.execute(()->{try{JSONObject result=request(path,data);ui(()->{if(generation==mine)done.accept(result);});}catch(SecurityException e){ui(()->{if(generation==mine)disconnect();});}catch(Exception e){if(generation==mine)notice("Invio non confermato · "+connectionProblem(e)+" · nessun reinvio automatico.");}});}
    void send(String value){
        if(!accountAuthenticated){showAccountGate("Accedi per riprendere la sessione.");return;}
        if(sending)return;
        if(session.isEmpty()){notice("Attendi la sincronizzazione della sessione prima di inviare");return;}
        if(!pendingId.isEmpty()){
            delivery("Verifico la ricevuta dell'ultimo invio · nessun messaggio reinviato");
            final int mine=generation;network.execute(()->{try{JSONObject state=request(receiptStatePath(),null);ui(()->{if(generation!=mine)return;
                if(!resolveDelivery(state))new AlertDialog.Builder(this).setTitle("Esito invio da verificare").setMessage("Nessuna ricevuta trovata per l'ID preciso: "+pendingId+". Non prova che la richiesta non sia stata eseguita. Conservo la bozza e non reinvio automaticamente. Puoi archiviare la verifica per continuare la chat; prima di ripetere un'azione finanziaria controlla le operazioni sul PC.").setPositiveButton("Archivia e libera chat",(d,w)->archivePending("Verifica archiviata · esito sconosciuto")).setNegativeButton("Attendi",null).show();
            });}catch(Exception e){if(generation==mine)notice("Verifica invio non riuscita · nessun reinvio automatico");}});return;
        }
        pendingId=UUID.randomUUID().toString();pendingText=value;saveCache();transmit(pendingId,value);
    }
    void transmit(String id,String value){
        sending=true;sendButton.setEnabled(false);delivery("Invio al PC in corso…");final int mine=generation;
        final String sentSession=session;
        network.execute(()->{try{JSONObject r=request("/v1/input",new JSONObject().put("request_id",id).put("text",value).put("session",sentSession).put("session_handoff",true));if(!id.equals(r.optString("request_id")))throw new IOException("Receipt ID mismatch");ui(()->{if(generation==mine){if("handoff".equals(r.optString("state"))&&r.optBoolean("not_enqueued")){pendingId="";pendingText="";switchCoordinator(r.optJSONObject("coordinator"),r.optString("session"));}else acceptDelivery(id,r.optString("state","queued"));}});}
            catch(InputRejectedException e){ui(()->{if(generation==mine&&pendingId.equals(id))archivePending("Richiesta rifiutata dal PC · HTTP 400 · controlla testo o comando");});}
            catch(Exception e){ui(()->{if(generation==mine)delivery("Esito invio non confermato · premi Verifica invio · nessun reinvio automatico");});}
            finally{ui(()->{sending=false;sendButton.setEnabled(true);sendButton.setText(pendingId.isEmpty()?"Invia":"Verifica invio");});}});
    }
    void acceptDelivery(String id,String state){
        if(!pendingId.equals(id))return;
        if(input.getText().toString().trim().equals(pendingText))input.setText("");pendingId="";pendingText="";
        lastRequestId=id;lastDeliveryLabel="";showReceipt(state,"");sendButton.setText("Invia");saveCache();
    }
    void showReceipt(String state,String outcome){
        String label="running".equals(state)?"In esecuzione sul PC":"queued".equals(state)?"Ricevuto dal PC · in coda":"interrupted_not_replayed".equals(state)?"Interrotto dal riavvio del PC · nessuna azione ripetuta":"finished".equals(state)?("completed".equals(outcome)?"Completato sul PC":"failed".equals(outcome)?"Richiesta fallita sul PC · consulta la chat":"interrupted".equals(outcome)?"Richiesta interrotta sul PC":"incomplete".equals(outcome)?"Richiesta incompleta sul PC":"Terminato sul PC · verifica il risultato in chat"):"Stato invio da verificare";
        if(!label.equals(lastDeliveryLabel)){lastDeliveryLabel=label;delivery(label);}
    }
    boolean resolveDelivery(JSONObject state){
        JSONObject exact=state.optJSONObject("request_receipt");
        if(exact!=null&&exact.optBoolean("found")){
            JSONObject r=exact.optJSONObject("receipt");String id=exact.optString("request_id");
            if(r!=null&&id.equals(r.optString("request_id"))){
                if(!pendingId.isEmpty()&&pendingId.equals(id)){acceptDelivery(id,r.optString("state"));showReceipt(r.optString("state"),r.optString("outcome"));return true;}
                if(pendingId.isEmpty()&&id.equals(lastRequestId))showReceipt(r.optString("state"),r.optString("outcome"));
            }
        }
        JSONArray requests=state.optJSONArray("requests");if(requests!=null)for(int i=0;i<requests.length();i++){JSONObject r=requests.optJSONObject(i);if(r==null)continue;String id=r.optString("request_id");
            if(!pendingId.isEmpty()&&pendingId.equals(id)){acceptDelivery(pendingId,r.optString("state"));showReceipt(r.optString("state"),r.optString("outcome"));return true;}
            if(pendingId.isEmpty()&&!lastRequestId.isEmpty()&&lastRequestId.equals(id))showReceipt(r.optString("state"),r.optString("outcome"));
        }return pendingId.isEmpty();
    }
    void startPolling(){
        if(running||token.isEmpty()||!accountAuthenticated)return;running=true;final int mine=++generation;
        polling.execute(()->{
            int failures=0;
            while(running&&generation==mine&&!destroyed){
                try{
                    JSONObject state=request(receiptStatePath(),null);
                    if(generation!=mine)break;
                    String instance=state.optString("instance");boolean switched=!session.equals(state.optString("session"));boolean reset=!transcriptLoaded||switched||cursor>state.optLong("cursor")||(!instance.isEmpty()&&!bridgeInstance.isEmpty()&&!instance.equals(bridgeInstance));
                    long after=reset?state.optLong("cursor"):cursor;
                    JSONObject events=request("/v1/events?after="+after,null);
                    if(events.optBoolean("gap")){reset=true;after=state.optLong("cursor");events=request("/v1/events?after="+after,null);}
                    if(generation!=mine)break;
                    final boolean rebuild=reset;final JSONObject received=events;final long nextCursor=events.optLong("cursor",after);failures=0;
                    CountDownLatch applied=new CountDownLatch(1);
                    ui(()->{try{if(generation==mine){render(state,rebuild);JSONArray rows=received.optJSONArray("events");if(rows!=null){StringBuilder chunk=new StringBuilder();for(int i=0;i<rows.length();i++){JSONObject row=rows.optJSONObject(i);String kind=row.optString("kind"),value=row.optString("text");chunk.append(kind.equals("user")?"\n❯ ":"").append(value);if(!kind.equals("stream"))chunk.append("\n");}append(chunk.toString());}session=state.optString("session");bridgeInstance=state.optString("instance");cursor=nextCursor;transcriptLoaded=true;}}finally{applied.countDown();}});
                    while(generation==mine&&!destroyed&&!applied.await(100,TimeUnit.MILLISECONDS)){}
                }catch(AccountRequiredException e){ui(()->{if(generation==mine)showAccountGate(e.getMessage());});break;}
                catch(SecurityException e){ui(()->{if(generation==mine)showAccountGate("Abbinamento non verificato: usa Riprendi o un codice nuovo dal PC. Il collegamento salvato è conservato.");});break;}
                catch(Exception e){if(generation!=mine)break;failures++;ui(()->{connected=false;notice("PC non raggiungibile · "+connectionProblem(e)+" · riconnessione senza reinvii");financeStatus.setText("Trading · stato PC non verificabile");try{tradingState.put("state","stale");}catch(JSONException ignored){}fillCards(tradingCards);});try{Thread.sleep(Math.min(15000,1000L<<Math.min(failures-1,4)));}catch(InterruptedException stop){break;}}
            }
        });
    }
    String amount(JSONObject value,String field){return value.has(field)&&!value.isNull(field)?value.optString(field):"non verificato";}
    void renderTrading(JSONObject value){
        if(value==null){tradingState=new JSONObject();tradingHeader.setText("Hyperliquid · dati conto non disponibili");tradingDetails.setText("Dati conto non disponibili · nessun saldo o posizione dedotti");fillCards(tradingCards);return;}
        tradingState=value;String phase=value.optString("state","unconfigured");boolean observed=value.has("retrieved_ms");
        String title="Conto reale · "+value.optString("network","");
        if(observed)title+=" · "+amount(value,"collateral_usdc")+" USDC";
        else title+=" · "+(value.optBoolean("refreshing")?"lettura in corso":phase.equals("unconfigured")?"nessun profilo":"dati non disponibili");
        if(phase.equals("stale"))title+=" · dati precedenti";
        tradingHeader.setText((tradingExpanded?"▾ ":"▸ ")+title);
        StringBuilder text=new StringBuilder();
        if(observed){
            text.append(value.optString("profile")).append(" · ").append(value.optString("account_mode")).append("\nCollaterale: ").append(amount(value,"collateral_usdc")).append(" USDC\nDisponibile prudenziale: ").append(amount(value,"available_usdc")).append(" USDC\n");
            JSONArray positions=value.optJSONArray("positions"),orders=value.optJSONArray("orders");
            text.append("Posizioni: ").append(positions==null?"non verificate":positions.length()==0?"nessuna":"");
            if(positions!=null)for(int i=0;i<positions.length();i++){JSONObject p=positions.optJSONObject(i);if(p==null)continue;JSONObject lev=p.optJSONObject("leverage");text.append("\n").append(p.optString("coin")).append(" · size ").append(p.optString("szi")).append(" · ").append(lev==null?"leva non verificata":lev.optString("value")+"x").append(" · PnL ").append(p.optString("unrealizedPnl","non verificato"));}
            text.append("\nOrdini aperti: ").append(orders==null?"non verificati":orders.length()==0?"nessuno":"");
            if(orders!=null)for(int i=0;i<orders.length();i++){JSONObject o=orders.optJSONObject(i);if(o==null)continue;text.append("\n").append(o.optString("coin")).append(" · ").append(o.optString("orderType","ordine")).append(" · trigger ").append(o.optString("triggerPx","—")).append(" · reduce-only ").append(o.optBoolean("reduceOnly"));}
            text.append("\nAggiornato ").append(java.text.DateFormat.getTimeInstance().format(new java.util.Date(value.optLong("retrieved_ms")))).append(" · ").append(value.optLong("age_seconds")).append("s fa");
        }
        if(value.optBoolean("refreshing"))text.append("\nAggiornamento in corso…");
        if(value.has("error"))text.append("\nLettura fallita: ").append(value.optString("error"));
        text.append("\nSola lettura dei dati conto · analisi e preparazione richiedono Invio. Ferma nuovi ingressi agisce sul servizio, senza chiudere posizioni.");tradingDetails.setText(text);fillCards(tradingCards);
    }
    void render(JSONObject state,boolean rebuild){
        latestState=state;connected=true;serviceState=state.optJSONObject("trading_service");renderTrading(state.optJSONObject("trading"));
        models=state.optJSONArray("models");sessions=state.optJSONArray("sessions");modelButton.setText(state.optString("model","auto"));String mode=state.optString("mode","ask");modeButton.setText(mode.equals("auto")?"Auto strumenti":mode.equals("plan")?"Pianifica":"Conferma azioni");
        String sessionTitle="Conversazione";if(sessions!=null)for(int i=0;i<sessions.length();i++){JSONObject row=sessions.optJSONObject(i);if(row!=null&&state.optString("session").equals(row.optString("id"))){sessionTitle=TradingPresentation.sessionTitle(row);break;}}
        String path=state.optString("project").replace('\\','/');project.setText(sessionTitle+" · "+path.substring(path.lastIndexOf('/')+1));project.setMaxLines(1);project.setEllipsize(android.text.TextUtils.TruncateAt.END);
        status.setText("● PC collegato · sessione sincronizzata");financeStatus.setText(TradingPresentation.service(serviceState));
        JSONObject telemetry=state.optJSONObject("request_progress");String activity=state.optBoolean("busy")?"AI in esecuzione":"AI pronta";
        String actual=state.isNull("actual_model")?"":state.optString("actual_model");
        if(telemetry!=null){String current=telemetry.isNull("actual_model")?"":telemetry.optString("actual_model");JSONObject usage=telemetry.optJSONObject("usage");
            activity+=" · "+(current.isEmpty()?"routing "+state.optString("model","auto")+" · modello non riportato":current)+" · "+String.format(Locale.ROOT,"%.1fs",telemetry.optDouble("elapsed_seconds"));
            activity+=" · "+(usage==null||usage.length()==0?"token in attesa del server":usage.has("total_tokens")?usage.optLong("total_tokens")+" token riportati":"token parziali "+usage.toString());
        }else activity+=" · "+(actual.isEmpty()?"modello non riportato":"ultimo modello "+actual)+" · "+state.optLong("tokens")+" token sessione riportati";
        assistantStatus.setText(activity+" · API "+state.optString("api","non verificata"));assistantStatus.setContentDescription(activity+" · API "+state.optString("api")+" · /auto non autorizza il trading");resolveDelivery(state);
        if(rebuild){StringBuilder historyText=new StringBuilder();JSONArray history=state.optJSONArray("messages");if(history!=null)for(int i=0;i<history.length();i++){JSONObject m=history.optJSONObject(i);historyText.append(m.optString("role").equals("user")?"\n❯ ":"\n✦ ").append(m.optString("content")).append("\n");}scroll.replaceText(historyText.toString(),!transcriptLoaded||!session.equals(state.optString("session")));}
        JSONObject q=state.optJSONObject("question");if(q==null){questionBox.removeAllViews();questionScroll.setVisibility(View.GONE);questionId="";return;}
        questionScroll.setVisibility(View.VISIBLE);
        if(questionId.equals(q.optString("id")))return;questionId=q.optString("id");questionBox.removeAllViews();questionBox.addView(text(q.optString("question"),15));
        JSONArray options=q.optJSONArray("options");if(options!=null)for(int i=0;i<options.length();i++){final String answer=options.optString(i);questionBox.addView(button(answer,()->answer(q.optString("id"),answer,false)));}
        EditText free=edit("Oppure scrivi una risposta libera");questionBox.addView(free);LinearLayout row=new LinearLayout(this);questionBox.addView(row);row.addView(button("Rispondi",()->answer(q.optString("id"),free.getText().toString(),false)));row.addView(button("Annulla",()->answer(q.optString("id"),"",true)));
    }
    void answer(String id,String value,boolean cancel){try{post("/v1/answer",new JSONObject().put("id",id).put("answer",value).put("cancelled",cancel),r->{questionBox.removeAllViews();questionId="";});}catch(Exception e){notice("Risposta non inviata.");}}
    void chooseModel(){
        if(models==null)return;LinearLayout box=new LinearLayout(this);box.setOrientation(1);EditText filter=edit("Cerca tra i modelli");box.addView(filter);ListView list=new ListView(this);box.addView(list,new LinearLayout.LayoutParams(-1,dp(360)));ArrayList<String> values=new ArrayList<>();for(int i=0;i<models.length();i++)values.add(models.optString(i));
        ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_list_item_1,values);list.setAdapter(adapter);AlertDialog dialog=new AlertDialog.Builder(this).setTitle("Modello della sessione PC").setView(box).setNegativeButton("Annulla",null).create();
        filter.addTextChangedListener(new android.text.TextWatcher(){public void beforeTextChanged(CharSequence s,int st,int c,int a){}public void onTextChanged(CharSequence s,int st,int before,int count){adapter.getFilter().filter(s);}public void afterTextChanged(android.text.Editable e){}});
        list.setOnItemClickListener((p,v,pos,id)->{send("/model "+adapter.getItem(pos));dialog.dismiss();});dialog.show();
    }
    void chooseSession(){if(!accountAuthenticated||sessions==null)return;final JSONArray choices=sessions;String[] labels=new String[choices.length()];for(int i=0;i<labels.length;i++)labels[i]=TradingPresentation.sessionLabel(choices.optJSONObject(i));sessionPicker=new AlertDialog.Builder(this).setTitle("Riprendi una conversazione").setItems(labels,(d,w)->send("/resume "+choices.optJSONObject(w).optString("id"))).setNeutralButton("Nuova sessione",(d,w)->new AlertDialog.Builder(this).setMessage("Iniziare una nuova sessione? La precedente resta salvata sul PC.").setPositiveButton("Nuova",(a,b)->send("/new")).setNegativeButton("Annulla",null).show()).setNegativeButton("Chiudi",null).show();}
}
