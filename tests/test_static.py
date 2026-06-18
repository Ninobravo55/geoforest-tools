"""
Smoke tests estáticos para GeoForest Tools — corren SIN QGIS.

No instancian los algoritmos (eso requiere el entorno QGIS), pero sí
verifican vía AST/pyflakes las clases de error que históricamente han
roto el plugin:

  1. Todo .py parsea (sin SyntaxError).
  2. Cero `undefined name` en todo el árbol  ← atrapa el bug NameError
     de glcluc que motivó esta consolidación.
  3. Cada *_algorithm.py define los métodos obligatorios del contrato
     QgsProcessingAlgorithm (name, displayName, createInstance, ...).
  4. Los algoritmos migrados a la base NO contienen bloques de export
     a mano (`ee.batch.Export`) — deben delegar en export_router/base.
  5. El provider registra exactamente las clases que existen.

Para tests de integración reales (ejecutar processAlgorithm con un AOI
de prueba contra GEE), usar el job de QGIS-Docker descrito en el README
de desarrollo; esos tests requieren credenciales y no corren en CI público.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "core"

PY_FILES = sorted(
    p for p in ROOT.rglob("*.py")
    if "__pycache__" not in p.parts
)

ALGO_FILES = sorted(CORE.glob("*_algorithm.py"))

MIGRATED_TO_BASE = [
    "glad_canopy_algorithm.py",
    "eth_canopy_algorithm.py",
    "meta_canopy_algorithm.py",
    "planet_canopy_algorithm.py",
]


def test_all_files_parse():
    """1. Ningún archivo tiene SyntaxError."""
    for p in PY_FILES:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def test_no_undefined_names():
    """2. pyflakes no reporta 'undefined name' en ningún archivo.

    Esta es la red de seguridad contra el bug que rompía glcluc.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", *map(str, PY_FILES)],
        capture_output=True, text=True,
    )
    undefined = [
        ln for ln in result.stdout.splitlines() if "undefined name" in ln
    ]
    assert not undefined, "Nombres no definidos detectados:\n" + "\n".join(undefined)


def test_algorithms_have_required_methods():
    """3. Cada algoritmo cumple el contrato mínimo de Processing."""
    required = {"name", "displayName", "createInstance", "initAlgorithm",
                "processAlgorithm", "group", "groupId"}
    for p in ALGO_FILES:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        # Métodos definidos directamente o heredados de la base.
        defined = {
            n.name
            for cls in tree.body if isinstance(cls, ast.ClassDef)
            for n in cls.body if isinstance(n, ast.FunctionDef)
        }
        inherits_base = "GeeRasterAlgorithm" in p.read_text(encoding="utf-8")
        missing = required - defined
        if inherits_base:
            # processAlgorithm/group/groupId los aporta la base.
            missing -= {"processAlgorithm", "group", "groupId"}
        assert not missing, f"{p.name} sin métodos: {sorted(missing)}"


def test_migrated_algorithms_have_no_handrolled_export():
    """4. Los algoritmos migrados delegan el export; no lo hacen a mano."""
    for name in MIGRATED_TO_BASE:
        src = (CORE / name).read_text(encoding="utf-8")
        assert "ee.batch.Export" not in src, (
            f"{name} todavía tiene export a mano; debe usar la base."
        )
        assert "GeeRasterAlgorithm" in src, f"{name} no hereda de la base."


def test_no_handrolled_export_anywhere():
    """4b. Ningún algoritmo del plugin usa `ee.batch.Export` directo.

    Tras la consolidación v2.0, TODO export va por la base o por
    export_router. Este guard impide que reaparezca el patrón duplicado.
    """
    offenders = [
        p.name for p in CORE.glob("*.py")
        if "ee.batch.Export" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "Estos archivos reintrodujeron export a mano (usar export_router/base): "
        + ", ".join(offenders)
    )


def test_provider_registers_existing_classes():
    """5. Las clases que el provider registra existen en sus módulos."""
    provider_src = (CORE / "geobosques_provider.py").read_text(encoding="utf-8")
    # addAlgorithm(XxxAlgorithm())
    import re
    registered = re.findall(r"addAlgorithm\((\w+)\(\)\)", provider_src)
    # Recolectar todas las clases definidas en core/*.py
    defined_classes = set()
    for p in ALGO_FILES:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        defined_classes |= {
            n.name for n in tree.body if isinstance(n, ast.ClassDef)
        }
    missing = [c for c in registered if c not in defined_classes]
    assert not missing, f"Provider registra clases inexistentes: {missing}"


if __name__ == "__main__":
    # Permite correr sin pytest: python tests/test_static.py
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
