from src.etl.load.main import LoadStats, run_load
from src.etl.load.reader import LoadedDocument, iter_processed

# A função `main` não é reexportada de propósito: o nome sombrearia o submódulo
# `src.etl.load.main`, e `import src.etl.load.main` passaria a devolver a função
# em vez do módulo. Quem precisa dela importa do módulo direto, como o
# __main__.py já faz.
__all__ = ["iter_processed", "LoadedDocument", "LoadStats", "run_load"]
