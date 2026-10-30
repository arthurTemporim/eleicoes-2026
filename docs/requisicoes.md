# Requisições ao TSE

Este documento descreve como os scripts conversam com o Portal de Dados Abertos do TSE. São dois serviços:

- **API CKAN** (`dadosabertos.tse.jus.br/api/3/action/package_show`): lista os arquivos de cada pacote.
- **CDN** (`cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2026/...`): serve os arquivos e aceita requisições parciais (`Range: bytes=a-b`, resposta `206 Partial Content`).

Veja também a [arquitetura](arquitetura.md) e o [fluxo dos dados](fluxo-de-dados.md).

## 1. Listar arquivos de um pacote

A API fica atrás de um CDN que guarda as respostas por até 1 h (`cache-control: max-age=3600`). Sem cuidado, ela devolve uma versão antiga do pacote, sem os arquivos publicados depois. Foi assim que o BU do 2º turno "sumiu" na primeira tentativa. O parâmetro `_=<timestamp>`, que a API ignora, muda a URL e força uma resposta atualizada.

```mermaid
sequenceDiagram
    autonumber
    participant S as download.py / process_urna.py
    participant C as Cache do CDN
    participant A as API CKAN

    S->>C: GET package_show?id=PACOTE&_=TIMESTAMP
    Note over C: URL nova, sem cache
    C->>A: repassa
    A-->>C: 200 JSON com resources[]
    C-->>S: 200 JSON
    S->>S: filtra URLs por turno e UF<br/>(regex _2t_UF)
```

## 2. Extrair um CSV de dentro de um zip remoto (resultados)

O `votacao_candidato_munzona_2026.zip` tem 399 MB, mas só o membro `_BR.csv` (38 MB) interessa. O `zipfile` da biblioteca padrão recebe um `HttpFile`, um objeto "arquivo" que transforma cada `seek` + `read` em uma requisição Range.

```mermaid
sequenceDiagram
    autonumber
    participant D as download.py
    participant Z as zipfile.ZipFile
    participant H as HttpFile
    participant CDN

    D->>H: HttpFile(url)
    H->>CDN: HEAD url
    CDN-->>H: Content-Length
    D->>Z: ZipFile(HttpFile)
    Z->>H: seek(fim − 22) + read
    H->>CDN: GET Range: últimos bytes
    CDN-->>H: 206 End of Central Directory
    Z->>H: seek(início do diretório central) + read
    H->>CDN: GET Range: diretório central
    CDN-->>H: 206 lista de membros e offsets
    D->>Z: open("..._BR.csv")
    loop blocos de até 8 MB até o fim do membro
        Z->>H: read(n)
        H->>CDN: GET Range: pos..pos+n
        CDN-->>H: 206 bytes comprimidos
        Z-->>D: bytes descomprimidos
        D->>D: grava em ..._BR.csv.part
    end
    Note over Z: zipfile confere o CRC ao final
    D->>D: renomeia .part → .csv
```

## 3. Baixar o Boletim de Urna (download retomável)

Os zips do BU são baixados inteiros, porque o CSV inteiro é necessário. O download é retomável e conferido com o `.sha512` publicado pelo TSE.

```mermaid
sequenceDiagram
    autonumber
    participant D as download.py
    participant CDN
    participant FS as dataset/raw/bu

    alt zip final já existe
        D->>FS: pula
    else
        D->>FS: tamanho de bweb_2t_UF.zip.part (N, ou 0)
        D->>CDN: GET url (Range: bytes=N- se N > 0)
        alt 206 Partial Content
            CDN-->>D: bytes a partir de N
            D->>FS: anexa ao .part
        else 200 OK (servidor ignorou o Range)
            CDN-->>D: arquivo inteiro
            D->>FS: reescreve o .part
        else 416 (o .part já está completo)
            CDN-->>D: nada a enviar
        end
        D->>CDN: GET url.sha512
        CDN-->>D: hash esperado
        D->>D: SHA-512 do .part
        alt confere
            D->>FS: renomeia .part → .zip
        else não confere
            D->>FS: apaga o .part e reporta erro
        end
    end
```

## 4. Leitura seletiva dos arquivos de urna (`process_urna.py`)

Esse é o passo pesado: 28 zips, 124 GB, ~500 mil seções. Para cada UF:

1. ler o índice do zip remoto, como no item 2;
2. dividir as seções em lotes de 8, na ordem física do zip;
3. distribuir os lotes para 32 processos, cada um com sua própria `Session` HTTP.

```mermaid
sequenceDiagram
    autonumber
    participant M as processo principal
    participant W as worker (×32)
    participant CDN

    M->>CDN: GET package_show (ver item 1)
    M->>CDN: HEAD + Range: diretório central do zip da UF
    CDN-->>M: lista de membros (~5 por seção)
    M->>M: agrupar_secoes: log.jez + rdv.dat por seção<br/>lotes de 8 seções
    M->>W: Pool(32, initializer): Session HTTP + spec ASN.1

    par cada worker, em paralelo
        M->>W: lote de 8 seções
        W->>W: ordena membros por offset<br/>junta intervalos com gap ≤ 16 KB
        loop um GET por grupo contíguo
            W->>CDN: GET Range: bytes=a-b
            CDN-->>W: 206 local headers + dados comprimidos
        end
        W->>W: lê local header, inflate (zlib −15), confere CRC32
        W->>W: processar_log + processar_rdv
        W-->>M: agregados das 8 seções + bytes lidos
    end

    M->>M: grava log_hora, log_secoes,<br/>rdv_tipos, rdv_nulos (_t2_UF.parquet)
```

### Como uma requisição cobre vários arquivos

Dentro do zip, os membros de uma seção (`bu.dat`, `rdv.dat`, `vota.vsc`, `log.jez`, imagem do BU) ficam próximos, mas a ordem entre eles varia de seção para seção. O `ler_membros` faz assim:

1. ordena os membros pedidos pelo offset;
2. calcula o intervalo de cada um: local header (30 bytes), nome, até 1 KB de campo extra e os dados comprimidos;
3. junta intervalos separados por até 16 KB.

Os bytes do meio, de membros que não interessam, só são transferidos quando o intervalo é pequeno. Um membro grande no meio, como a assinatura `vota.vsc`, separa os pedidos em duas requisições.

```mermaid
flowchart TD
    Pedidos["membros pedidos do lote<br/>log.jez e rdv.dat de 8 seções"] --> Ordena["ordena por header_offset"]
    Ordena --> Gap{"distância até o fim<br/>do grupo atual ≤ 16 KB?"}
    Gap -- "sim" --> Junta["estende o grupo atual"]
    Gap -- "não" --> Novo["fecha o grupo e abre outro"]
    Junta --> Prox["próximo membro"]
    Novo --> Prox
    Prox -- "há mais" --> Gap
    Prox -- "acabou" --> Fim["um GET Range por grupo<br/>fatia cada membro do bloco"]
```

Se o campo extra do local header passar de 1 KB, uma segunda requisição busca só os dados daquele membro.

## Tratamento de falhas

| Situação | Tratamento |
|---|---|
| 429 ou 5xx do CDN, falha de conexão | `urllib3.Retry`: até 8 tentativas com backoff exponencial (fator 1 s), em `GET` e `HEAD` |
| Resposta sem `206` ou com tamanho diferente do pedido | `IOError`. A seção é marcada com erro em `log_secoes.erro`. |
| CRC diferente após descompressão | `IOError`, mesmo tratamento |
| Erro em um lote | só as seções do lote são marcadas, e a UF segue |
| Interrupção do `process_urna.py` | retoma: UFs com os 4 Parquets prontos são puladas |
| Interrupção do download do BU | retoma pelo `.part` com `Range: bytes=N-` |
| Falha de um zip do BU | os demais seguem, e a saída lista as falhas com código diferente de zero |
| Lista de arquivos desatualizada | parâmetro anti-cache `_` na API CKAN |

## Volume e tempo (2º turno, medidos)

| Etapa | Requisições | Bytes transferidos | Tempo |
|---|---|---|---|
| Resultados (`_BR.csv`) | poucas dezenas | ~42 MB | ~1 min |
| BU (27 zips) | 54 (zip + `.sha512`) | 0,14 GB | alguns minutos |
| Arquivos de urna (28 UFs) | da ordem de 1 a 2 GETs por seção, mais o índice de cada UF | ~89 GB | ~30–45 min com 50–76 MB/s |
