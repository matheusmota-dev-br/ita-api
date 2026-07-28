from enum import Enum

from sqlalchemy import Enum as SAEnum


class Phase(str, Enum):
  """Fase da prova.

  UNICA cobre 2008–2018, quando o vestibular era fase única com um PDF por
  matéria. POS_1SEM/POS_2SEM cobrem o processo seletivo da pós-graduação.
  """

  PRIMEIRA = "1f"
  SEGUNDA = "2f"
  UNICA = "unica"
  POS_1SEM = "pos_1sem"
  POS_2SEM = "pos_2sem"


class DocumentKind(str, Enum):
  PROVA = "prova"
  GABARITO = "gabarito"


class ReviewStatus(str, Enum):
  """Quem produziu o conteúdo e se alguém validou.

  RAW = só extração heurística. LLM_GENERATED = o LLM normalizou, ninguém olhou.
  HUMAN_VERIFIED = revisado. REJECTED = extração ruim, mantida como tombstone
  (apagar faria o próximo ETL recriá-la).
  """

  RAW = "raw"
  LLM_GENERATED = "llm_generated"
  HUMAN_VERIFIED = "human_verified"
  REJECTED = "rejected"


class DisciplineSource(str, Enum):
  """Como a matéria de uma questão foi determinada."""

  FILENAME = "filename"
  SECTION_HEADER = "section_header"
  ANSWER_KEY = "answer_key"
  LLM = "llm"
  HUMAN = "human"


class AssetKind(str, Enum):
  QUESTION_CROP = "question_crop"
  SUPPORT_CROP = "support_crop"
  REGION = "region"


class RunStatus(str, Enum):
  RUNNING = "running"
  SUCCESS = "success"
  FAILED = "failed"


class NormalizationStatus(str, Enum):
  OK = "ok"
  INVALID_JSON = "invalid_json"
  SCHEMA_ERROR = "schema_error"
  API_ERROR = "api_error"
  REFUSED = "refused"


def enum_col(enum_cls: type[Enum], **kwargs) -> SAEnum:
  """Enum como VARCHAR + CHECK (o SQLite não tem tipo enum nativo).

  `values_callable` é obrigatório: sem ele o SQLAlchemy grava o *nome* do membro
  (PRIMEIRA) em vez do valor ("1f"), o que faz o conteúdo do banco divergir
  silenciosamente do conteúdo dos JSONs do ETL.

  `create_constraint` virou False por padrão no SQLAlchemy 1.4. Sem ele a coluna
  sai como VARCHAR puro e o banco aceita qualquer string — a validação só
  existiria do lado do Python, e o ETL escreve via SQL cru.
  """
  return SAEnum(
    enum_cls,
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda cls: [member.value for member in cls],
    name=enum_cls.__name__.lower(),
    **kwargs,
  )
