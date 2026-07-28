import sys

from src.etl.transform.main import main

if __name__ == "__main__":
  # `sys.exit(main())` e não `main()`: sem propagar o código de saída, um
  # transform que falha termina com 0 e passa por sucesso num `&&` ou no CI.
  # Os outros três entry points já faziam isso; este era a exceção.
  sys.exit(main())
