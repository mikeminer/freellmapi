# Solana in freellmapi

Sono installati due ambienti separati dal runtime Hyperliquid:

- Python: `solana 0.42.0`, `solders 0.29.0`, `truststore 0.10.5`; versioni complete in `requirements-lock.txt`.
- Node: `@solana/kit 8.6.0` e `solana-agent-kit 2.0.10`, con lockfile. Import dei moduli verificato senza creare wallet o caricare chiavi.
- Skill ufficiale della Solana Foundation `solana-dev-976fb4a3`, con 37 file di documentazione e riferimenti, installata nello store di freellmapi. Nessuno script della skill è stato eseguito.

Il catalogo Awesome Solana AI è una raccolta di progetti, non un unico SDK installabile. È consultabile con `solana_sdk_catalog`; i protocolli elencati non diventano automaticamente connettori finanziari attivi.

## Importazione del wallet dedicato

Sul PC, nel CMD di freellmapi:

```text
/solana
```

Nel pannello Python scegli il nome e la rete, poi importa la chiave privata base58 di 64 byte o il JSON keypair esistente. Puoi indicare l'indirizzo pubblico atteso: una chiave diversa viene rifiutata. Non vengono generati altri wallet. Non inserire chiavi o seed in chat, nei prompt o nei comandi shell.

La chiave resta mascherata nel campo finché non lo svuoti o chiudi il pannello; viene salvata cifrata con Windows DPAPI per lo stesso utente Windows. Non è una sandbox contro altri processi eseguiti con il tuo utente. Nessuna chiave è passata ai modelli, a Node Agent Kit o all'app Android.

Il valore iniziale è **devnet**. Puoi scegliere mainnet nel pannello; reti, profili e ricevute restano distinti da Hyperliquid. Il saldo Solana non è il collaterale Hyperliquid e non viene sommato implicitamente.

## Capacità effettivamente collegate

| Funzione | Stato |
|---|---|
| Saldo SOL e token SPL / Token-2022 | Lettura RPC tramite `solana_read` |
| Conti, transazioni e stato firme pubbliche | Lettura RPC |
| Simulazione di transazioni serializzate | Sola simulazione, senza firma o invio |
| Trasferimento SOL | Anteprima, simulazione e revisione esatta nel pannello locale |
| Firma e invio | Solo dal pannello Python, dopo conferma del trasferimento specifico |
| Swaps Jupiter, perps Drift, staking, NFT, bridge | SDK e riferimenti disponibili per sviluppo; connettori di esecuzione non implementati |
| Trading automatico Solana h24 | Non implementato o attivato da questa installazione |

Esempi di richieste nella chat:

```text
Leggi il saldo del mio wallet Solana e i token che contiene. Solo lettura.
Mostrami le ultime transazioni del profilo wallet-dedicato.
Mostrami gli SDK Solana e distingui gli strumenti collegati dai protocolli ancora da implementare.
```

`/solana status`, `/solana operations` e `/solana sdk` sono disponibili nel CMD. Il processo SDK invocato dagli strumenti accetta soltanto status/read/prepare/simulate/operations. Non ha comandi IPC di importazione della chiave o firma/invio. Un invio incerto registra la firma pubblica e blocca trasferimenti sostitutivi sullo stesso account/rete; non viene ripetuto automaticamente. Submitted non equivale a confirmed.

## Dipendenze Node

L'audit del pacchetto Agent Kit 2.0.10 segnala 23 advisory nelle dipendenze, di cui 17 high e nessuna critical. Il downgrade 2.0.1 suggerito dall'audit è stato provato e scartato: il pacchetto pubblicato non ha un entry point importabile. È stata conservata la versione importabile in un ambiente di sviluppo separato. **Il wallet privato non è collegato ad Agent Kit**; la policy è in `runtime-policy.json`. Non sono stati eseguiti lifecycle script NPM né applicati aggiornamenti forzati.

Le operazioni finanziarie native implementate usano il percorso Python separato. Le prove SDK sono sintetiche, con wallet temporanei e RPC simulati; non sono stati inviati trasferimenti reali. Le connessioni RPC pubbliche devnet e mainnet sono state verificate in sola lettura, compresi gli hash genesis.

Fonti: [Solana Python SDK](https://michaelhly.com/solana-py/), [Solders](https://kevinheavey.github.io/solders/), [Solana Agent Kit](https://github.com/sendaifun/solana-agent-kit), [Awesome Solana AI](https://github.com/solana-foundation/awesome-solana-ai).
