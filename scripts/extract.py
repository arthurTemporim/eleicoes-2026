"""Extrai apenas os .csv dos zips do Boletim de Urna (dataset/raw/bu/) para dataset/<nome do zip>/.

Uso:
    python scripts/extract.py
"""
import zipfile

from datasets import DATASET_DIR, RAW_DIR


def extrair_csvs(zip_path, destino_dir):
    with zipfile.ZipFile(zip_path) as z:
        membros = [m for m in z.infolist() if m.filename.lower().endswith(".csv")]
        for m in membros:
            alvo = destino_dir / m.filename.rsplit("/", 1)[-1]
            if alvo.exists() and alvo.stat().st_size == m.file_size:
                continue
            destino_dir.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(alvo, "wb") as dst:
                while bloco := src.read(1 << 20):
                    dst.write(bloco)
    return len(membros)


def main():
    for zip_path in sorted((RAW_DIR / "bu").glob("*.zip")):
        n = extrair_csvs(zip_path, DATASET_DIR / zip_path.stem)
        print(f"{zip_path.name}: {n} csv")


if __name__ == "__main__":
    main()
