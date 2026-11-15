# FREELLMAPI Remote 0.2.4

La chat rimane la schermata principale. Le sessioni, la memoria e i progetti sono sul PC; il telefono conserva l'abbinamento Android Keystore e una cache cifrata della conversazione visualizzata, della bozza e della posizione di lettura.

## Uso quotidiano

- In alto sono separati collegamento PC, assistente e servizio trading. «Auto strumenti» non autorizza ordini reali. Il modello effettivo e i token appaiono solo quando riportati dal server. Il tempo è misurato sul PC.
- Tocca il riepilogo del conto per aprire le schede di setup e posizioni. Sono mostrati solo i dati reali osservati. Il monitor resta attivo anche sotto STOP; usa gli stessi limiti e l'autorizzazione dell'account della chat, senza una seconda approvazione del servizio. Non esiste una modalità di trading virtuale.
- **Analizza** prepara una richiesta per il mercato scelto; **Prepara ingresso** richiede un piano condizionato con conferma, scadenza, TP/SL e scenario contrario. Entrambi lasciano la richiesta nel campo di testo: premi Invia per mandarla. Preparare un piano non arma il monitor né autorizza un ordine.
- **Ferma nuovi ingressi** contatta direttamente il servizio, separatamente dalla coda AI. La conferma arriva dal PC. Non chiude posizioni e non cancella ordini aperti. In caso di rete assente l'arresto è indicato come non confermato.
- **Ferma AI** interrompe la risposta dell'assistente; il monitor finanziario ha un controllo separato.
- **Dettagli / altro** apre un pannello dal basso con percorso e ID sessione, lettura conto, limiti, setup e ricevute native, nuova sessione e scollegamento. I dettagli sono una fotografia con data di rilevamento.
- **Sessioni** mostra titolo, progetto, ultimo aggiornamento e numero di messaggi. Le domande del PC mantengono una scelta alla volta e risposta libera alternativa.

## Protezioni osservate

I prezzi TP/SL di un setup sono *previsti*. Una posizione è indicata protetta soltanto rispetto a ordini osservati sullo stesso mercato, lato opposto, reduce-only, tipo trigger TP o stop, con quantità sufficiente. Copertura insufficiente è parziale. Un ordine limite reduce-only non viene chiamato stop o TP trigger. Letture precedenti, in aggiornamento o incomplete non certificano protezioni. Nessun target garantisce profitto o perdita massima.

## Continuità e invii

Rotazione, sfondo e ricreazione dell'Activity conservano chat, bozza e punto di lettura. La cache cifrata permette il ripristino anche dopo la chiusura del processo. Il PC resta la fonte canonica della cronologia. Cache e credenziali sono separate; un cambio PC non recupera la cache di un altro endpoint.

La riconnessione esegue solo GET. Non reinvia messaggi, risposte, stop o ordini. Dopo un invio incerto, **Verifica invio** cerca l'ID preciso anche oltre le ultime 15 richieste. L'assenza di ricevuta non prova che l'azione non sia stata eseguita. **Archivia e libera chat** conserva ID e testo cifrati nei Dettagli e libera l'invio senza rispedire il vecchio messaggio. Se il testo incerto è ancora nel campo, viene tolto dal campo e conservato nell'archivio; una bozza diversa resta al suo posto. Un rifiuto HTTP 400 viene distinto da un timeout e conserva il testo. Il cambio di istanza del bridge o il reset del cursore forza la sincronizzazione della cronologia conservando l'ancora di lettura.

## Collaudo

`build_apk.py` compila il client nativo e verifica la firma. `build_ui_tests.py` e `run_ui_tests.py` esercitano l'APK su emulatore con conto e richieste sintetici. Il runner rifiuta telefoni reali perché cancella solo i dati dell'app di test nell'emulatore. Sul Seeker si installa esclusivamente con `adb -s SERIAL install -r`, stessa firma e stesso package, senza disinstallazione o pulizia dati.
