from enum import Enum


class OperatingSystem(Enum):
  MACOS = "darwin"
  LINUX = "linux"
  WINDOWS = "windows"


class Architecture(Enum):
  X64 = "x64"
  ARM64 = "arm64"


OS_ARCH_MAP = {
  OperatingSystem.MACOS: {
    Architecture.X64: "mac-x64",
    Architecture.ARM64: "mac-arm64",
  },
  OperatingSystem.LINUX: {
    Architecture.X64: "linux-x64",
    Architecture.ARM64: "linux-arm64",
  },
  OperatingSystem.WINDOWS: {
    Architecture.X64: "win-x64",
    Architecture.ARM64: "win-arm64",
  },
}


# str, Enum para que os valores sirvam direto como query params da API e como
# valores de coluna no SQLite.
class Discipline(str, Enum):
  FISICA = "fisica"
  MATEMATICA = "matematica"
  QUIMICA = "quimica"
  PORTUGUES = "portugues"
  INGLES = "ingles"
  # Coluna própria nas provas de 2ª fase de 2019–2024.
  REDACAO = "redacao"


class Language(str, Enum):
  PORTUGUES = "portugues"
  INGLES = "ingles"


class TestType(str, Enum):
  VESTIBULAR = "vestibular"
  POS = "pos"


class FormatType(str, Enum):
  """Formato da questão.

  OBJECTIVE = múltipla escolha A–E (1ª fase). DISSERTATIVE = resposta aberta
  (2ª fase). ESSAY = redação, que não tem numeração de questão.
  """

  OBJECTIVE = "objective"
  DISSERTATIVE = "dissertative"
  ESSAY = "essay"
