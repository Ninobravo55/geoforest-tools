"""
Wrapper único de inicialización de Google Earth Engine.

Antes: 20 algoritmos repetían:

    if not getattr(ee.data, '_credentials', None):
        settings = QgsSettings()
        project_id = settings.value('geoforesttools/gee_project', '', type=str)
        if project_id: ee.Initialize(project=project_id)
        else: ee.Initialize()

Y en el peor caso (early_warning) se llegaba a invocar ee.Authenticate()
dentro del hilo de Processing, lo cual BLOQUEA QGIS porque abre navegador
y espera input del usuario en un thread sin event loop.

Esta función centraliza la inicialización, NO llama jamás a
ee.Authenticate() (eso es responsabilidad exclusiva del diálogo GUI de
autenticación), y lanza un mensaje útil que dirige al usuario a la
pantalla correcta.
"""

from qgis.core import QgsProcessingException, QgsSettings, QgsTask, QgsApplication, QgsMessageLog, Qgis


SETTINGS_KEY_PROJECT = 'geoforesttools/gee_project'

_AUTH_HELP_MSG = (
    "No se pudo conectar a Google Earth Engine.\n\n"
    "Posibles causas:\n"
    "  1. Aún no has autenticado con Google. Ve al menú "
    "'GeoForest Tools → Autenticación GEE' y completa el proceso.\n"
    "  2. El ID de Proyecto de Google Cloud configurado no es válido o "
    "no tiene la Earth Engine API habilitada.\n"
    "  3. Tu cuenta no está aprobada para Earth Engine. Solicita acceso en "
    "https://earthengine.google.com/\n\n"
    "Detalle técnico: {error}"
)


def ensure_gee_initialized(feedback=None, force=False):
    """
    Asegura que Earth Engine esté inicializado en el proceso actual.

    Parámetros
    ----------
    feedback : QgsProcessingFeedback | None
        Si se proporciona, se publica un mensaje informativo en el log
        de Processing.
    force : bool
        Si True, re-inicializa aunque ya existan credenciales.

    Lanza
    -----
    QgsProcessingException
        Con instrucciones claras si la inicialización falla.
    """
    import ee  # import diferido — evita fallar en plugins sin GEE instalado

    if not force and getattr(ee.data, '_credentials', None) is not None:
        return  # ya inicializado

    if feedback is not None:
        feedback.pushInfo("Inicializando Google Earth Engine...")

    settings = QgsSettings()
    project_id = settings.value(SETTINGS_KEY_PROJECT, '', type=str)

    try:
        if project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
    except Exception as e:  # ee.EEException u otras
        # Importante: NO llamamos a ee.Authenticate() aquí. Eso abriría
        # un navegador desde el hilo de Processing y colgaría QGIS.
        raise QgsProcessingException(_AUTH_HELP_MSG.format(error=str(e)))


def is_initialized():
    """True si Earth Engine ya está inicializado en este proceso."""
    try:
        import ee
        return getattr(ee.data, '_credentials', None) is not None
    except ImportError:
        return False

class GEEInitTask(QgsTask):
    def __init__(self, force=False):
        super().__init__("Inicializando Earth Engine (GeoForest)", QgsTask.CanCancel)
        self.force = force
        self.exception = None

    def run(self):
        try:
            import ee
            if not self.force and getattr(ee.data, '_credentials', None) is not None:
                return True
            settings = QgsSettings()
            project_id = settings.value(SETTINGS_KEY_PROJECT, '', type=str)
            if project_id:
                ee.Initialize(project=project_id)
            else:
                ee.Initialize()
            return True
        except Exception as e:
            self.exception = str(e)
            return False

    def finished(self, result):
        if result:
            QgsMessageLog.logMessage("GEE Inicializado silenciosamente en segundo plano.", "GeoForestTools", Qgis.Success)
        else:
            QgsMessageLog.logMessage(f"Fallo al inicializar GEE en segundo plano (puede requerir autenticación): {self.exception}", "GeoForestTools", Qgis.Warning)

def background_gee_initialize(force=False):
    """Lanza la inicialización de GEE en un hilo en segundo plano."""
    try:
        task = GEEInitTask(force=force)
        QgsApplication.taskManager().addTask(task)
    except Exception:
        pass
