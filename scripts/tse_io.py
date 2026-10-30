"""Leitura padronizada dos CSVs do TSE.

Formato (ver leiame do TSE): Latin-1, separador ';', todos os campos entre aspas.
#NULO / #NE são sentinelas de texto; -1 / -3 são os equivalentes numéricos.
"""
from pathlib import Path

import pandas as pd

from datasets import DATASET_DIR, PROCESSED_DIR  # noqa: F401  (reexportados para o notebook)

SENTINELAS_TEXTO = ["#NULO", "#NE", "#NULO#"]
SENTINELAS_NUM = [-1, -3]


def ler_csv(caminho, usecols=None, dtype=None, **kwargs):
    """Lê um CSV do TSE. Sentinelas de texto viram NA."""
    return pd.read_csv(
        caminho,
        sep=";",
        encoding="latin-1",
        usecols=usecols,
        dtype=dtype,
        na_values=SENTINELAS_TEXTO,
        keep_default_na=False,
        low_memory=False,
        **kwargs,
    )


def ler_csvs(padrao, **kwargs):
    """Lê e concatena todos os CSVs que casam com o glob (relativo a dataset/)."""
    arquivos = sorted(Path(DATASET_DIR).glob(padrao))
    if not arquivos:
        raise FileNotFoundError(f"nenhum arquivo em dataset/{padrao} — rode scripts/download.py e extract.py")
    return pd.concat([ler_csv(a, **kwargs) for a in arquivos], ignore_index=True)


def ler_parquets(padrao):
    """Lê e concatena os parquets de dataset/processed/ que casam com o glob."""
    arquivos = sorted(Path(PROCESSED_DIR).glob(padrao))
    if not arquivos:
        raise FileNotFoundError(f"nenhum arquivo em dataset/processed/{padrao} — rode scripts/process_urna.py")
    return pd.concat([pd.read_parquet(a) for a in arquivos], ignore_index=True)
