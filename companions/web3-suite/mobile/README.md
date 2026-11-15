# freellmapi Remote per Android

## Accesso Tailscale / Google · versione 0.2.8

Dopo l’aggiornamento premi **Accedi con Tailscale / Google**. PC e Seeker devono
essere connessi a Tailscale con lo stesso account proprietario. Il PC verifica
l’identità del telefono tramite il suo indirizzo sorgente e Tailscale WhoIs;
il telefono non dichiara da solo email o identità. Nessuna password Google,
chiave wallet o chiave dei modelli viene inserita nell’app. **Apri Tailscale**
apre l’app installata, dove completi personalmente l’eventuale login Google.

Il pulsante **Account** apre nome, account ed **Esci da FREELLMAPI**. L’uscita
blocca solo questa app, conserva credenziale Remote e cache cifrata e non
disconnette Tailscale, CMD, desktop o attività già accettate dal PC. Il logout
rimane anche al riavvio: per rientrare premi Accedi. Chat, bozza, scorrimento,
ricevute e invii incerti vengono recuperati dopo la verifica dell’identità,
senza reinviare messaggi. **Collegamento al PC** torna alle impostazioni di
abbinamento senza cancellare quello salvato. Scollega rimane un’azione distinta.

Il bridge PC deve essere aggiornato: un bridge precedente viene segnalato e non
viene trattato come un accesso riuscito. È SSO tramite la rete Tailscale, non un
nuovo OAuth Google dentro FREELLMAPI. Il tunnel USB di collaudo non dimostra
l’identità Tailscale del telefono: per il login reale usa l’URL Tailscale del PC.

Quando scegli una chat già aperta in un altro coordinatore dello stesso PC,
l’app si collega al coordinatore che la possiede. Il trasferimento conserva il
token di abbinamento e verifica host, trasporto, fingerprint HTTPS se presente,
istanza e sessione; non accoda un secondo `/resume` e non ripete vecchi ordini.
CMD e Desktop usano la stessa risoluzione. Dopo l’aggiornamento riapri i vecchi
client CMD per caricare il codice nuovo: un client precedente non viene lasciato
in attesa di una ricevuta di trasferimento che non sa interpretare.

La versione **0.2.0** aggiunge schede di setup/posizioni, stati separati di PC/AI/trading, arresto diretto dei nuovi ingressi, dettagli in un pannello dal basso, sessioni leggibili e cache locale cifrata di chat/bozza/scroll. Comportamento delle protezioni e recupero degli invii: [UX.md](UX.md).

L'app controlla la sessione freellmapi sul PC. Il telefono invia messaggi e riceve risposte, attività degli strumenti, stato API e consumi riportati. Puoi selezionare i modelli disponibili sul server, creare/riprendere sessioni e rispondere a una domanda o conferma per volta, anche con testo libero. Il codice, la vault Obsidian e le credenziali dei provider rimangono sul PC. Non richiede Claude Code né un abbonamento a un altro agente.

## Installazione

APK firmato per installazione diretta: `build/freellmapi-remote.apk`, Android 8.0/API 26 o successivo. È una build debug per uso personale, non una pubblicazione Play Store. Il sorgente nativo Java non usa WebView, JavaScript remoto o SDK social.

## Da fuori casa

1. Installa e collega **Tailscale sul PC e sul telefono**, allo stesso account/rete privata. È il trasporto VPN; non gestisce i modelli, le sessioni o le chiavi freellmapi. [Guida ufficiale](https://tailscale.com/docs/how-to/quickstart).
2. Con VPN attiva, apri CMD nella cartella del progetto ed esegui:

   ```bat
   freellmapi --remote-only
   ```

   Riprende l'ultima sessione di quel progetto, oppure ne crea una se assente. Con `--new` ne crea una nuova; con `--session ID` riprende quella specificata. I permessi iniziali restano `ask`, salvo esplicito `--auto`.

3. Il programma rileva l'IP Tailscale del PC e ascolta **solo su quell'indirizzo**, porta 31416. Mostra link, codice monouso e percorso dell'immagine QR. Il codice scade dopo 120 secondi e dopo otto tentativi errati. Genera un nuovo codice con `/remote pair` da una sessione CMD interattiva.
4. Installa l'APK, aprilo e incolla il link mostrato sul PC. In alternativa scansiona il QR con la fotocamera del telefono e aprilo con freellmapi Remote. Nell'app premi **Collega al PC**. Non serve inserire la chiave API dei modelli.
5. Lascia accesi PC, freellmapi e VPN. Sul telefono puoi passare dal Wi-Fi alla connessione dati: l'indirizzo privato resta quello della VPN. Non serve aprire una porta sul router. Il funzionamento fuori casa richiede che la VPN permetta telefono → PC:31416; il firewall Windows deve consentire questa connessione privata.

Per usare anche la tastiera del PC, avvia normalmente `freellmapi`, poi `/remote start` e `/remote qr`. Il prompt colorato interattivo riceve anche i messaggi del telefono. In terminali senza prompt interattivo usa `--remote-only`, che non attende input sulla console.

In `--remote-only` i plugin e gli MCP configurati non vengono caricati prima dell’abbinamento, per evitare conferme su una console nascosta. Dopo esserti collegato puoi inviare `/reload` dall’app: le conferme di caricamento arrivano sul telefono. Le autorizzazioni non vengono concesse automaticamente.

La modalità VPN usa HTTP **all'interno del tunnel cifrato WireGuard di Tailscale**, esclusivamente sull'IP VPN effettivamente rilevato. L'app rifiuta questo trasporto se Android non segnala una VPN attiva o l'indirizzo non è della rete privata Tailscale. Non è una modalità HTTP pubblica e non si attiva su `0.0.0.0`. La connessione diretta LAN alternativa usa HTTPS con fingerprint del certificato verificato dall'app; non ignora certificati modificati da proxy o antivirus.

## Collegamento USB e collaudo locale

Con debug USB autorizzato sul telefono:

```bat
adb reverse tcp:31416 tcp:31416
freellmapi --remote-only --remote-bind usb
```

Il server ascolta solo su 127.0.0.1. Il tunnel ADB permette all'app di raggiungere il loopback del PC. Questa modalità non dà accesso da fuori casa. L'emulatore va selezionato esplicitamente con `adb -s emulator-5554 reverse ...` quando ci sono più dispositivi.

## Comandi nel CMD

- `/remote start`: avvia sulla VPN Tailscale rilevata; senza VPN avvia HTTPS solo locale e lo segnala.
- `/remote start usb`: avvia sul loopback per tunnel USB, senza cambiare il firewall.
- `/remote start IP`: HTTPS su uno specifico IP, oppure tunnel VPN quando corrisponde all'IP Tailscale verificato.
- `/remote pair`: rigenera codice, link e QR; il precedente codice non è più valido.
- `/remote pair https://IP:31416`: imposta l'indirizzo raggiungibile per un server HTTPS già avviato.
- `/remote qr`: apre il QR sul PC con il visualizzatore immagini.
- `/remote status`: mostra indirizzo e numero di dispositivi.
- `/remote revoke`: revoca tutti gli abbinamenti e svuota la coda; il lavoro già iniziato non viene riavvolto.
- `/remote stop`: chiude il collegamento, conservando la sessione CMD.

Le autorizzazioni sono legate al dispositivo abbinato. Sul PC viene conservato solo l'hash della credenziale remota; sul telefono è cifrata con una chiave AES nel Keystore Android. L'abbinamento dura 30 giorni. **Scollega** nell'app elimina la credenziale dal telefono; la revoca sul PC invalida anche eventuali copie. Nessun token unificato FreeLLMAPI o chiave wallet è restituito dalle API remote.

Il codice a otto cifre è **monouso**: dopo l'abbinamento non va reinserito e il vecchio link non serve più. L'app riprende il collegamento salvato al riavvio e riconosce anche un vecchio link dello stesso PC senza riutilizzarne il codice. Un tentativo di abbinamento fallito conserva la connessione precedente; **Riprendi collegamento salvato** permette di tornarci. Nel primo campo puoi incollare il link completo oppure l'URL semplice del PC; nel secondo caso inserisci anche il codice attuale. Il fingerprint non serve nella modalità VPN privata. **Apri Tailscale · accesso Google** apre l'app Tailscale installata: il login Google avviene lì, mentre FREELLMAPI conserva il proprio abbinamento al PC. Il manifest dichiara la visibilità del solo pacchetto Tailscale per Android 11 e successivi. Se l'app manca o non è apribile nel profilo corrente, compare un avviso: la pagina di download si apre soltanto scegliendola esplicitamente.

**Riprendi collegamento salvato** compare solo se il telefono ha già completato e conservato un abbinamento. Per il primo collegamento usa URL e codice nuovo. Non lasciare nel primo campo un vecchio link completo: contiene il codice precedente. Puoi sostituirlo con il solo URL del PC e inserire il codice attuale nel campo dedicato.

Se il codice è scaduto, apri un altro CMD sul PC ed esegui:

```bat
freellmapi --remote-pair --pair-seconds 600
```

Il comando genera un nuovo codice monouso valido dieci minuti nel bridge già aperto, anche in `--remote-only`. Non richiede API dei modelli, non cambia sessione e non riavvia il lavoro. La durata predefinita resta 120 secondi; il massimo è 600, con lo stesso limite di tentativi. La richiesta passa soltanto da file locali nel profilo Windows: non viene aggiunto un endpoint di generazione codici accessibile dal telefono. Un vecchio processo Remote va aggiornato riavviandolo una volta prima di usare questa funzione.

La porta è esclusiva: una seconda istanza non può ascoltare sullo stesso indirizzo e porta. Il comando di rinnovo verifica anche che il server raggiungibile sia la stessa istanza che genera il codice; se trova un conflitto o una vecchia versione, si ferma con un errore esplicito. Per usare una porta diversa avvia, ad esempio, `freellmapi --remote-only --remote-port 31418` e usa nell'app l'URL completo mostrato da quel processo, compresa la porta.

## Comportamento e limiti

- È lo stesso agente sul PC: non vengono creati agenti concorrenti per eseguire i messaggi. La coda è limitata e le richieste hanno ID persistenti per impedire il reinvio automatico dopo una disconnessione o un riavvio.
- In `ask` le azioni richieste dal telefono aspettano **Consenti/Nega** nell'app. Le domande hanno opzioni e alternativa libera; una risposta scaduta non viene applicata a una domanda successiva. Le azioni iniziate dalla tastiera del PC continuano a chiedere conferma sul PC.
- I cambi di modello e sessione passano dallo stesso coordinatore e conservano la cronologia esistente sul PC. L'app mostra gli ultimi messaggi; l'intero archivio rimane sul computer.
- Le letture si riconnettono automaticamente con attesa crescente. Gli invii non confermati **non** vengono ritentati automaticamente. Dopo un riavvio del PC, i messaggi accettati ma non completati sono marcati `interrupted_not_replayed`; non vengono eseguiti nuovamente alla cieca.
- **Interrompi** richiede l'interruzione del turno, non annulla operazioni esterne già eseguite o in volo. Il ritardo dipende dalla chiamata in corso. Non chiude posizioni finanziarie.
- L'app funziona in primo piano. Tornando all'app rilegge stato e conversazione. Questa versione non ha notifiche push, trasferimento di allegati o replica dei pannelli desktop Hyperliquid/Blender. I pannelli credenziali restano locali.
- La chat segue il testo in arrivo quando sei in fondo. Se scorri ai messaggi precedenti mantiene la posizione; **↓ Ultimi messaggi** torna alla risposta attuale. L'invio di un tuo messaggio riattiva il seguito dei nuovi messaggi. Il testo rimane selezionabile.
- La rotazione conserva chat, bozza, domanda attiva e collegamento; i controlli si compattano in orizzontale. In caso di ricreazione della schermata Android vengono ripristinati cronologia visibile, posizione, bozza e cursore degli eventi. Le credenziali restano cifrate tramite il Keystore e non sono copiate nello stato della schermata. Il cursore avanza dopo che gli eventi sono stati applicati all'interfaccia, senza reinviare comandi.
- Il PC deve essere acceso e la sessione CMD aperta. Non è un servizio cloud autonomo, non modifica firewall/router e non configura automaticamente account VPN.

## Compilare e verificare

```bat
C:\Python312\python.exe -X utf8 mobile\build_apk.py
C:\Python312\python.exe -X utf8 -m unittest test_remote_access
```

Il build usa JDK 17, Android SDK Platform 35 e Build Tools 35.0.0 presenti sul PC, senza scaricare package Gradle o dipendenze Android. Segue gli strumenti ufficiali [AAPT2](https://developer.android.com/tools/aapt2), [D8](https://developer.android.com/tools/d8) e apksigner. `build/build-result.json` contiene hash, dimensione e verifica della firma.

Il collaudo `qa_smoke.py` usa un emulatore, il vero APK e il vero CLI, con risposte modello predeterminate e HOME/progetto isolati. Verifica abbinamento, conferma da app, scrittura/lettura reale di un file, cambio modello, nuova sessione, resume, riconnessione e revoca. Non chiama modelli reali e non effettua operazioni finanziarie.

`qa_tailscale_launch.py --tailscale-apk PERCORSO_APK_UFFICIALE --output CARTELLA` verifica il pulsante Tailscale su un emulatore Android 11 o successivo. Controlla l'app effettivamente in primo piano, il caso Tailscale disabilitata, l'annullamento del download e la riapertura dopo riabilitazione. Rifiuta dispositivi fisici e non effettua login né attiva VPN. `--before-apk` permette di confrontare una build precedente sullo stesso emulatore. Il test cancella i dati FREELLMAPI dell'emulatore e ripristina Tailscale se l'ha disabilitata.

`build_ui_tests.py` compila un APK di strumentazione separato, escluso dall'app distribuita. `run_ui_tests.py --output CARTELLA` lo esegue soltanto su un emulatore e cancella i dati dell'app di prova. I controlli usano il vero componente Android con testo sintetico: fondo della chat, aggiornamenti incrementali, lettura precedente, tastiera effettivamente aperta, rotazione in entrambi i versi, bozza, ricreazione forzata e selezione del testo. Non chiamano modelli reali né usano la memoria o le credenziali dell'utente.
