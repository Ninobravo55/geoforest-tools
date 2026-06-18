"""
Capa de compatibilidad Qt5 / Qt6 (y por extensión QGIS 3.x ↔ 4.x).

PyQt6 elimina varias APIs que PyQt5 exponía. QGIS 3.28/3.34 usan PyQt5,
QGIS 4.x usa PyQt6. Este módulo centraliza los shims para que un solo
codebase funcione en ambos sin `try/except` esparcidos.

Patrones cubiertos:
  1. QVariant.X → QMetaType.Type.X (tipos de campo en QgsField)
  2. exec_() → exec()  (QDialog, QApplication)
  3. Qt.WindowContextHelpButtonHint → Qt.WindowType.WindowContextHelpButtonHint
  4. QProcess.MergedChannels → QProcess.ProcessChannelMode.MergedChannels
  5. Qt.AlignCenter, Qt.Horizontal, etc → namespaces de enum

Filosofía:
  - Las constantes se resuelven UNA SOLA VEZ en import-time.
  - Si una constante no existe en la versión actual, se cae al
    equivalente. Si tampoco existe, se levanta ImportError claro.
  - No usar try/except dispersos en el código consumidor. Importar
    desde aquí y punto.
"""

from qgis.PyQt.QtCore import Qt


# ---------------------------------------------------------------------------
# 1. Tipos de campo para QgsField  (QVariant.X  →  QMetaType.Type.X)
# ---------------------------------------------------------------------------
#
# En PyQt5: QVariant.String = 10, QVariant.Int = 2, etc.
# En PyQt6: estas constantes se movieron a QMetaType.Type.QString,
#           QMetaType.Type.Int, etc.
#
# QgsField acepta el valor entero del enum en ambos casos, así que
# basta con resolver una constante numérica única y portable.

try:
    # PyQt6 / Qt6
    from qgis.PyQt.QtCore import QMetaType
    _QMT = QMetaType.Type

    QMETATYPE_STRING = _QMT.QString
    QMETATYPE_INT = _QMT.Int
    QMETATYPE_LONGLONG = _QMT.LongLong
    QMETATYPE_DOUBLE = _QMT.Double
    QMETATYPE_BOOL = _QMT.Bool
    QMETATYPE_DATE = _QMT.QDate
    QMETATYPE_DATETIME = _QMT.QDateTime

    _QT_BACKEND = 'QMetaType'

except (ImportError, AttributeError):
    # PyQt5 / Qt5 (QGIS 3.28 / 3.34)
    from qgis.PyQt.QtCore import QVariant

    QMETATYPE_STRING = QVariant.String
    QMETATYPE_INT = QVariant.Int
    QMETATYPE_LONGLONG = QVariant.LongLong
    QMETATYPE_DOUBLE = QVariant.Double
    QMETATYPE_BOOL = QVariant.Bool
    QMETATYPE_DATE = QVariant.Date
    QMETATYPE_DATETIME = QVariant.DateTime

    _QT_BACKEND = 'QVariant'


# ---------------------------------------------------------------------------
# 2. Wrapper de exec() / exec_()
# ---------------------------------------------------------------------------
#
# PyQt5 expone ambos `exec()` y `exec_()` (porque `exec` era palabra
# reservada en Python 2). PyQt6 sólo expone `exec()`. Wrapper único:

def dialog_exec(dialog):
    """
    Ejecuta un diálogo modal de forma portable Qt5/Qt6.

    Equivalente a `dialog.exec()` en PyQt6 y `dialog.exec_()` en PyQt5,
    eligiendo automáticamente. Devuelve el código de retorno del diálogo.
    """
    fn = getattr(dialog, 'exec', None) or getattr(dialog, 'exec_', None)
    if fn is None:
        raise RuntimeError(
            f"El objeto {type(dialog).__name__} no expone exec() ni exec_()"
        )
    return fn()


# ---------------------------------------------------------------------------
# 3. Qt enum-namespaces  (PyQt6 los movió a sub-namespaces)
# ---------------------------------------------------------------------------
#
# En PyQt5 se accede como `Qt.AlignCenter`, `Qt.Horizontal`, etc.
# En PyQt6 hay que usar `Qt.AlignmentFlag.AlignCenter`,
# `Qt.Orientation.Horizontal`, etc.
#
# Solución: para cada constante, intentamos primero el namespace nuevo
# y caemos al plano si no existe.

def _resolve_qt_enum(*candidates):
    """
    Intenta resolver una constante Qt buscando por varios paths.
    `candidates` es una secuencia de tuplas (namespace, attr_name)
    o strings con la ruta completa separada por puntos.
    """
    for cand in candidates:
        if isinstance(cand, str):
            parts = cand.split('.')
            obj = Qt
            try:
                for p in parts:
                    obj = getattr(obj, p)
                return obj
            except AttributeError:
                continue
    raise AttributeError(f"No se pudo resolver ninguna de: {candidates}")


# Alignment
ALIGN_CENTER = _resolve_qt_enum('AlignmentFlag.AlignCenter', 'AlignCenter')
ALIGN_LEFT = _resolve_qt_enum('AlignmentFlag.AlignLeft', 'AlignLeft')
ALIGN_RIGHT = _resolve_qt_enum('AlignmentFlag.AlignRight', 'AlignRight')
ALIGN_TOP = _resolve_qt_enum('AlignmentFlag.AlignTop', 'AlignTop')
ALIGN_BOTTOM = _resolve_qt_enum('AlignmentFlag.AlignBottom', 'AlignBottom')

# Orientation
ORIENTATION_HORIZONTAL = _resolve_qt_enum('Orientation.Horizontal', 'Horizontal')
ORIENTATION_VERTICAL = _resolve_qt_enum('Orientation.Vertical', 'Vertical')

# Window flags / hints
WINDOW_CONTEXT_HELP_HINT = _resolve_qt_enum(
    'WindowType.WindowContextHelpButtonHint',
    'WindowContextHelpButtonHint',
)


# ---------------------------------------------------------------------------
# 4. QProcess.MergedChannels  →  QProcess.ProcessChannelMode.MergedChannels
# ---------------------------------------------------------------------------

def qprocess_merged_channels():
    """
    Devuelve la constante `MergedChannels` apropiada para la versión de
    PyQt en uso. Se resuelve en tiempo de llamada para no fallar al
    importar el módulo si QProcess no estuviera disponible por algún
    motivo (ej. entornos QGIS-server headless).
    """
    from qgis.PyQt.QtCore import QProcess
    enum_ns = getattr(QProcess, 'ProcessChannelMode', None)
    if enum_ns is not None and hasattr(enum_ns, 'MergedChannels'):
        return enum_ns.MergedChannels  # PyQt6
    return QProcess.MergedChannels  # PyQt5


# ---------------------------------------------------------------------------
# 5. Diagnóstico — útil para debug y para `metadata.txt`
# ---------------------------------------------------------------------------

def qt_backend_info():
    """
    Devuelve una tupla (backend, qt_version_str) para diagnóstico.
    Útil para volcar al log de Processing cuando algo falla en Qt6.
    """
    from qgis.PyQt.QtCore import QT_VERSION_STR  # noqa: PyPep8
    return (_QT_BACKEND, QT_VERSION_STR)
