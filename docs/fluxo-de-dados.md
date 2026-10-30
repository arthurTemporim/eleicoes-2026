# Fluxo dos dados

Este documento mostra o caminho de cada dado: de onde vem (pacotes do TSE), como é processado (scripts) e como é analisado (notebook) até virar resposta.

Veja também a [arquitetura](arquitetura.md) e as [requisições](requisicoes.md).

## Visão completa

```mermaid
flowchart LR
    subgraph Fontes["Fontes: TSE"]
        R["resultados-2026<br/>detalhe_votacao_munzona_2026.zip<br/>votacao_candidato_munzona_2026.zip"]
        B["resultados-2026-boletim-de-urna<br/>bweb_2t_UF.zip + .sha512<br/>27 zips, 0,14 GB"]
        U["resultados-2026-arquivos-transmitidos-para-totalizacao<br/>bu_imgbu_logjez_rdv_vscmr_2026_2t_UF.zip<br/>28 zips, 124 GB"]
    end

    subgraph Proc["Processamento: scripts/"]
        D1["download.py<br/>extrai só os membros _BR<br/>via Range"]
        D2["download.py<br/>download retomável<br/>confere SHA-512"]
        E["extract.py<br/>só os .csv"]
        P["process_urna.py<br/>Range: log.jez + rdv.dat<br/>~89 GB lidos, 0 gravados"]
    end

    subgraph Arq["dataset/"]
        C1["*_munzona_2026_BR.csv<br/>45 MB"]
        C2["bweb_2t_UF.csv<br/>0,9 GB"]
        P1[("log_hora_t2_UF<br/>log_secoes_t2_UF")]
        P2[("rdv_tipos_t2_UF<br/>rdv_nulos_t2_UF")]
    end

    subgraph NB["Análise: notebook"]
        Q12["P1, P2<br/>votos 17h–19h"]
        Q34["P3, P4<br/>sem biometria"]
        Q5["P5<br/>composição dos votos<br/>Lula × Flávio"]
        Q6["P6<br/>ranking dos nulos"]
        V["Validação<br/>log e RDV × BU"]
        S[("secoes_presidente_t2.parquet<br/>base consolidada")]
        Est["Estimativas<br/>inferência ecológica"]
        Res["Resumo"]
    end

    R --> D1 --> C1
    B --> D2 --> E --> C2
    U --> P
    P --> P1
    P --> P2

    P1 --> Q12
    P1 --> Q34
    C2 --> Q34
    C1 --> Q5
    C2 --> Q5
    P2 --> Q6
    P1 --> V
    P2 --> V
    C2 --> V
    V --> S
    S --> Est
    Q12 --> Res
    Q34 --> Res
    Q5 --> Res
    Q6 --> Res
    V --> Res
    Est --> Res
```

`UF` nos nomes de arquivo representa a sigla da UF (`AC`, `SP`… e `ZZ` para o exterior).

## Que dado responde cada pergunta

| Pergunta | Dado | Por que esse dado |
|---|---|---|
| 1, 2. Votos entre 17h e 19h | log da urna | É o único que tem a hora de cada voto. Os CSVs de resultado e o BU não têm hora. |
| 3, 4. Votos sem biometria | log da urna + BU | O log mostra o tipo de habilitação de cada eleitor. O BU tem `QT_ELEI_BIOM_SEM_HABILITACAO`: quem tinha biometria cadastrada e não foi reconhecido. |
| 5. Composição dos votos | CSVs `munzona` + BU | Os CSVs têm os totais oficiais por município e zona. O BU tem o mesmo dado por seção, usado em Lula × Flávio. |
| 6. Números digitados nos nulos | RDV | Os CSVs agregam todo nulo como `96`. Só o RDV guarda o número digitado. |

## Processamento de uma seção

Cada seção do zip de urna tem cinco arquivos. O `process_urna.py` busca só dois deles.

```mermaid
flowchart TD
    Zip["zip da UF no CDN<br/>o{pleito}{uf}{município}{zona}{seção}-{tipo}.{ext}"] --> Sel{"tipo do membro"}
    Sel -- "log.jez (~72%)" --> Log["log.jez"]
    Sel -- "rdv.dat (~0,1%)" --> Rdv["rdv.dat"]
    Sel -- "vota.vsc, imgbu, bu.dat (~28%)" --> Ign["ignorado<br/>não é pedido"]

    Log --> Abre["abre zip ou 7z<br/>recursivo: logs arquivados"]
    Abre --> Lin["linhas de logd.dat<br/>dedup preservando ordem"]
    Lin --> Dia["filtra 25/10/2026"]
    Dia --> Maq["máquina de estados<br/>habilitação → voto"]
    Maq --> LH[("log_hora<br/>votos, votos_biometria,<br/>habilitados por hora local")]
    Maq --> LS[("log_secoes<br/>hora_pronta, n_urnas_log, erro")]

    Rdv --> Asn["decodifica ASN.1 BER<br/>EntidadeResultadoRDV"]
    Asn --> Pres["filtra cargo Presidente"]
    Pres --> RT[("rdv_tipos<br/>votos por tipo")]
    Pres --> RN[("rdv_nulos<br/>número digitado")]
```

### Máquina de estados do log

O log tem uma linha por evento, sem identificar o eleitor. O tipo de habilitação é registrado **antes** da habilitação, e cada voto computado herda o tipo da última habilitação.

```mermaid
stateDiagram-v2
    [*] --> Aguardando
    Aguardando --> TipoBiometrico: "Tipo de habilitação do eleitor [biométrica]"
    TipoBiometrico --> Aguardando: "Habilitação cancelada"
    TipoBiometrico --> HabilitadoBio: "Eleitor foi habilitado"
    Aguardando --> HabilitadoSemBio: "Eleitor foi habilitado"
    HabilitadoBio --> Aguardando: "O voto do eleitor foi computado"<br/>votos += 1, votos_biometria += 1
    HabilitadoSemBio --> Aguardando: "O voto do eleitor foi computado"<br/>votos += 1
```

A mensagem "Urna pronta para receber votos" marca a abertura, que é às 8h de Brasília. O notebook usa essa hora para inferir o fuso de cada município.

## Análise no notebook

```mermaid
flowchart TD
    LH[("log_hora")] --> Fuso["hora local → Brasília<br/>desloc = 8 − hora_pronta<br/>moda por município"]
    LS[("log_secoes")] --> Fuso
    Fuso --> P12["P1, P2: horas 17 e 18 de Brasília<br/>÷ total de votos do log"]
    LH --> P34["P3, P4: votos − votos_biometria"]

    BWEB["bweb_2t_*.csv"] --> BU[("bu_secoes_presidente_t2<br/>uma linha por seção")]
    BU --> P34
    MZ["munzona CSVs"] --> MZP[("munzona_presidente_t2<br/>candidato_munzona_presidente_t2")]
    MZP --> P5["P5: composição Brasil e UF<br/>soma 100% conferida"]
    BU --> LF["Lula × Flávio<br/>região, UF, município, seção"]

    RN[("rdv_nulos")] --> P6["P6: ranking dos números"]
    RT[("rdv_tipos")] --> Val["Validação seção a seção"]
    LH --> Val
    BU --> Val

    Val --> Sec[("secoes_presidente_t2<br/>BU + log + RDV + fuso")]
    Sec --> Est["Estimativas<br/>perfil das seções e<br/>regressão dentro do município"]

    P12 --> Resumo["Resumo"]
    P34 --> Resumo
    P5 --> Resumo
    LF --> Resumo
    P6 --> Resumo
    Val --> Resumo
    Est --> Resumo
```

### Caches em Parquet

Quando não encontra os CSVs, o notebook lê os Parquets versionados. Quando os CSVs existem e são mais novos que o Parquet, ele regera o Parquet.

```mermaid
flowchart LR
    Inicio(["ler dado"]) --> TemCSV{"CSV existe?"}
    TemCSV -- "não" --> TemPQ{"Parquet existe?"}
    TemPQ -- "sim" --> LePQ["lê Parquet versionado"]
    TemPQ -- "não" --> Erro["erro: rode download.py"]
    TemCSV -- "sim" --> Novo{"Parquet mais novo<br/>que o CSV?"}
    Novo -- "sim" --> LePQ
    Novo -- "não" --> LeCSV["lê CSV, filtra 2º turno + Presidente<br/>grava Parquet"]
```

## Validações embutidas

| Checagem | Onde | Resultado (2º turno) |
|---|---|---|
| CRC32 de cada membro lido via Range | `remote_zip.py` | toda leitura |
| SHA-512 dos zips do BU | `download.py` | 27 de 27 |
| Candidatos + brancos + nulos + demais = `QT_VOTOS` | notebook, P5 | Brasil e todas as UFs |
| Soma do BU por seção = total oficial de cada candidato | notebook, Lula × Flávio | exata |
| Brancos e nulos do RDV = BU | notebook, Validação | 100% das 499.205 seções |
| Votos do log = comparecimento do BU | notebook, Validação | 99,993% (28 seções sem log, 7 divergentes) |
