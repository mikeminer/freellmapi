# FREELLMAPI Desktop

Avvio su questo PC: icona **FREELLMAPI Desktop** oppure `python desktop_app.py`.
Il programma usa Qt/Python e FreeLLMAPI, senza dipendere da Codex o Claude Code.

CMD interattivo, app desktop e Seeker si collegano allo stesso coordinatore
Remote. Scegli la stessa sessione: messaggi, modello, permessi e domande sono
condivisi. Un solo motore esegue le richieste in coda e un blocco di processo
impedisce a un secondo coordinatore di scrivere la stessa sessione. Chiudere un
client non chiude il coordinatore. Il PC e il collegamento Tailscale devono
restare disponibili per il telefono. Un'altra sessione resta una chat distinta.

La chat offre Invio per inviare, Maiusc+Invio per andare a capo, allegati,
dettatura locale modificabile prima dell'invio, lettura vocale tramite Windows,
selezione modello, progetti e sessioni. Il pulsante Hyperliquid apre le azioni
guidate. Il segnale di sola analisi e l'esecuzione sono scelte distinte.

Gli allegati vengono associati alla sessione ed estratti a pagine con lo
strumento `attachment_read`. Le immagini vanno al modello che supporta la
visione. File audio: trascrizione locale allegata come testo. Nessun file viene
inviato premendo soltanto Allega o Microfono: rileggi la bozza e premi Invia.
Documenti e audio della chat inviata sono contesto per il modello scelto.

La dettatura usa Whisper base CPU/int8, separato dall'ambiente del CLI:
`pip install --target desktop/vendor -r desktop/requirements-audio.txt`.
Il primo avvio scarica i pesi. Gli errori di riconoscimento vanno corretti nella
bozza. Il collaudo automatico non sostituisce una prova del microfono fisico.

In caso di rete interrotta viene consultata la ricevuta dello stesso ID:
nessun reinvio automatico dei messaggi finanziari. Le bozze restano locali;
gli invii accettati hanno un registro persistente. Configurazioni e firme
rimangono nel backend locale e non vengono copiate nell'interfaccia web.

La finestra mostra la parte recente dello storico; l'archivio completo resta
nella cartella delle sessioni. Non è una copia del software proprietario Codex,
né una certificazione di equivalenza di tutte le sue funzioni.

## Impostazioni, organizzazione e lingue

Nelle impostazioni puoi aprire i connettori Hyperliquid e Solana e scegliere
quali sezioni mostrare nella barra laterale: Hyperliquid, Solana e DevFridge
Game Builder. Le preferenze sono persistenti. Nascondere una sezione non
spegne servizi, non scollega il wallet e non modifica ordini o autorizzazioni.

Il pulsante + crea un progetto; i menu ⋯ gestiscono progetti e conversazioni.
Puoi spostare le chat trascinandole sul progetto e i progetti trascinandoli su
altri progetti. Per riportarli al livello principale trascinali sull'intestazione
Progetti e conversazioni. I cicli padre/figlio vengono rifiutati. Le chat
cancellate sono recuperabili dal cestino delle impostazioni. Eliminare un
raggruppamento conserva chat e cartella fisica.

La gerarchia è salvata nel catalogo SQLite locale. Se la vault è collegata,
vengono aggiornate automaticamente note e collegamenti bidirezionali in
`freellmapi-memory/FREELLMAPI Desktop`. Le conversazioni originali non vengono
spostate. Mantieni annotazioni manuali fuori dai blocchi generati nelle note;
le note prive del blocco vengono preservate e segnalate come conflitto.

Il selettore lingua comprende le esatte 60 lingue dichiarate dal dashboard
ufficiale, con RTL per arabo, ebraico, persiano e urdu. I dizionari sono locali
e vengono caricati su richiesta; nessun traduttore viene contattato al cambio
lingua. Le traduzioni automatiche delle etichette aggiuntive non hanno avuto
revisione da madrelingua. I contenuti delle chat mantengono la lingua originale.

Il logo proviene dal repository ufficiale FreeLLMAPI. Attribuzione e licenza
sono in `third_party/FREELLMAPI-LICENSE.txt`; questi client sono complementari
al router originale.

## Account Tailscale / Google

Premi **Accedi con Tailscale / Google**: il client verifica l'identità già
autenticata nel Tailscale installato sul PC, incrociando stato del dispositivo
e `whois`. Se Tailscale è scollegato, **Apri Tailscale** ti permette di completare
l'accesso Google nella sua app e nel browser di sistema. FREELLMAPI non riceve
password o token Google. Questo è SSO locale tramite Tailscale, non un nuovo
provider OAuth/OIDC remoto e non sincronizza i file su Google.

L'account proprietario è vincolato a utente Tailscale, tailnet e utente Windows.
La sessione desktop viene protetta con Windows DPAPI. Il riavvio riprende
l'accesso finché l'identità è ancora valida; un account diverso non acquisisce
i progetti del proprietario. Disconnessione di Tailscale o cambio identità
bloccano il client. Il sistema protegge l'accesso nell'app: i file locali di
chat e vault mantengono i permessi Windows esistenti.

Nelle impostazioni, o premendo l'account in basso, **Esci** blocca soltanto
FREELLMAPI Desktop. Non disconnette la VPN, non arresta Remote o il lavoro già
accettato, non cancella chat, bozze o memoria, non modifica wallet o ordini.
Il nuovo accesso riaggancia il coordinatore e conserva l'abbinamento salvato.
Se CMD o Seeker hanno cambiato chat nel frattempo, mostra la sessione attiva
del coordinatore; le altre restano disponibili nella barra laterale.

Il logo ufficiale è usato dalla finestra, dall'icona globale Qt e dal collegamento
Windows, con lo stesso AppUserModelID per la barra delle applicazioni. Per
ricreare il collegamento: `python desktop/install_shortcut.py`. Python rimane
il runtime; la sua icona di sistema non viene modificata per gli altri programmi.
