package it.freellmapi.remote;

import org.json.*;
import java.math.BigDecimal;
import java.util.Locale;

/** Read-only presentation of native receipts, never an authority or order builder. */
final class TradingPresentation {
    static String value(JSONObject o,String key){return o!=null&&o.has(key)&&!o.isNull(key)?o.optString(key):"non verificato";}
    static BigDecimal decimal(String s){try{return new BigDecimal(s);}catch(Exception e){return null;}}
    static String service(JSONObject s){
        if(s==null)return "Trading: stato non disponibile";
        String mode=s.optString("mode");String state=s.optString("state");
        String label="running".equals(state)?"monitor attivo":"paused".equals(state)?"in pausa":"not_started".equals(state)?"non avviato":"fermo / heartbeat scaduto";
        return "Trading "+("live".equals(mode)?"LIVE":"disattivato · autorizzazione locale richiesta")+" · "+label+
            ("live".equals(mode)?(s.optBoolean("new_entries_stopped")?" · STOP nuovi ingressi":s.optBoolean("real_orders_allowed")?" · permessi account condivisi":" · monitoraggio · invii secondo permessi account"):"");
    }
    static String protection(JSONObject p,JSONObject account,String kind){
        if(!"ready".equals(account.optString("state"))||account.optBoolean("refreshing")||!account.has("retrieved_ms")||account.optJSONArray("orders")==null)return "non verificato · dati incompleti o precedenti";
        BigDecimal size=decimal(p.optString("szi"));if(size==null||size.signum()==0)return "non verificato";
        JSONArray orders=account.optJSONArray("orders");BigDecimal covered=BigDecimal.ZERO;StringBuilder prices=new StringBuilder();boolean partial=false;
        for(int i=0;i<orders.length();i++){
            JSONObject o=orders.optJSONObject(i);if(o==null||!p.optString("coin").equals(o.optString("coin"))||!o.optBoolean("reduceOnly")||!(size.signum()>0?"A":"B").equals(o.optString("side")))continue;
            String type=o.optString("orderType").toLowerCase(Locale.ROOT).replace(" ","");
            boolean matches="TP".equals(kind)?type.contains("takeprofit"):type.contains("stop")&&!type.contains("takeprofit");
            BigDecimal trigger=decimal(o.optString("triggerPx")),qty=decimal(o.optString("sz"));
            if(!matches||!o.optBoolean("isTrigger")||trigger==null||trigger.signum()<=0||qty==null||qty.signum()<=0)continue;
            partial=true;covered=covered.add(qty);if(prices.length()>0)prices.append(", ");prices.append(trigger.toPlainString());
        }
        if(covered.compareTo(size.abs())>=0)return "attivo osservato · "+prices;
        return partial?"copertura parziale osservata · "+prices:"mancante negli ordini osservati";
    }
    static String position(JSONObject p,JSONObject account){
        BigDecimal size=decimal(p.optString("szi"));JSONObject lev=p.optJSONObject("leverage");
        return p.optString("coin","?")+" · "+(size==null?"direzione non verificata":size.signum()>0?"LONG":"SHORT")+
            "\nQuantità "+value(p,"szi")+" · leva "+(lev==null?"non verificata":value(lev,"value")+"×")+
            "\nEntry "+value(p,"entryPx")+" · PnL "+value(p,"unrealizedPnl")+" USDC"+
            "\nTP: "+protection(p,account,"TP")+"\nSL: "+protection(p,account,"SL");
    }
    static String watch(JSONObject w){
        JSONObject t=w.optJSONObject("trigger");String state=w.optString("state");
        String phase="watching".equals(state)?"ATTESA LIVELLO":"triggering".equals(state)?"LIVELLO RAGGIUNTO · controlli in corso":"validated".equals(state)?"RISCHIO VERIFICATO":"prepared".equals(state)?"PREPARATO · invio non confermato":"dispatching".equals(state)||"sending".equals(state)?"INVIO DA VERIFICARE":"effect_observed".equals(state)?"ESITO OSSERVATO · verifica ricevuta":"done".equals(state)?"COMPLETATO · verifica ricevuta e posizione":"expired".equals(state)?"SCADUTO":"paused".equals(state)?"IN PAUSA":state.toUpperCase(Locale.ROOT);
        String confirmation=t==null?"non verificata":t.optString("confirmation");
        String confirm="close".equals(confirmation)?"chiusura candela "+t.optString("interval"):"retest".equals(confirmation)?"ritorno sul livello":"touch".equals(confirmation)?"tocco del livello":confirmation;
        return w.optString("coin","?")+" · "+("buy".equals(w.optString("side"))?"LONG":"sell".equals(w.optString("side"))?"SHORT":"direzione non verificata")+" · "+phase+
            "\nIngresso "+(t==null?"non verificato":("above".equals(t.optString("direction"))?"sopra ":"sotto ")+value(t,"level"))+" · "+confirm+
            "\nTP previsto "+value(w,"take_profit")+" · SL previsto "+value(w,"stop_loss")+
            "\nScadenza: "+(w.has("remaining_seconds")?w.optLong("remaining_seconds")+"s":"non verificata")+" · conferma: "+value(w,"confirmation_state")+
            "\nPiano → attesa → controlli rischio → invio → verifica esito"+
            "\nI prezzi previsti non provano la presenza di ordini sul mercato.";
    }
    static String sessionLabel(JSONObject row){
        String root=row.optString("root").replace('\\','/');int at=root.lastIndexOf('/');if(at>=0)root=root.substring(at+1);
        String updated=row.optString("updated","");
        return sessionTitle(row)+"\n"+root+(updated.isEmpty()?"":" · "+updated.replace('T',' '))+(row.has("messages")?" · "+row.optInt("messages")+" messaggi":"");
    }
    static String sessionTitle(JSONObject row){
        String title=row.optString("title","Conversazione").trim();String lower=title.toLowerCase(Locale.ROOT);
        if(lower.startsWith("conversation summary (")||lower.startsWith("summary for continuation")||lower.startsWith("[conversation summary")||lower.startsWith("riassunto della conversazione"))return "Conversazione ripresa";
        return title.isEmpty()?"Nuova conversazione":title;
    }
}
