# RSS Fisco & Contabilità — Artykul

Genera un unico RSS filtrato da fonti ufficiali: Gazzetta Ufficiale, Agenzia delle Entrate, Dipartimento delle Finanze/MEF e OIC.

## Pubblicazione gratuita con GitHub Pages

1. Crea un nuovo repository GitHub, ad esempio `rss-fiscale`.
2. Carica **tutto il contenuto di questa cartella**, inclusa `.github`.
3. In GitHub vai in **Settings → Actions → General → Workflow permissions** e seleziona **Read and write permissions**.
4. Vai in **Actions → Aggiorna feed fiscale → Run workflow**. Al primo avvio verrà creato `docs/feed.xml`.
5. Vai in **Settings → Pages** e scegli **Deploy from a branch** → branch `main` → cartella `/docs`.
6. Dopo la pubblicazione, il feed sarà:

   `https://TUO-USERNAME.github.io/rss-fiscale/feed.xml`

7. Incolla quell'URL in Artykul come sorgente RSS.

## Come funziona il filtro

Il file `config.yml` contiene pesi per IVA, IRPEF, IRES, IRAP, TUIR, dichiarazioni, F24, ritenute, crediti d'imposta, accertamento/riscossione, contabilità, bilancio, OIC, IMU, registro, bollo e altro.

Una voce viene pubblicata solo se raggiunge la soglia `min_score`. I termini palesemente estranei possono sottrarre punti. Le news OIC vengono mantenute con priorità perché la fonte è già fortemente specialistica.

## Aggiornamento

GitHub Actions esegue il controllo ogni 2 ore. Il PC personale può essere spento.

## Nota importante

Questo è un aggregatore informativo. I link degli elementi puntano sempre alla fonte originaria. Per uso professionale, verificare sempre il testo ufficiale e la vigenza dell'atto.
