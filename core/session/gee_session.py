"""
GEESession — singleton thread-safe para Earth Engine.

Se mantiene por compatibilidad con código que importe directamente
desde core.session. La inicialización efectiva delega en
utils.gee_init.ensure_gee_initialized() para que exista UNA sola ruta
de inicialización en todo el plugin.
"""

import threading


class GEESession:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> 'GEESession':
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def initialize(self, feedback=None, force: bool = False) -> None:
        """
        Inicializa Earth Engine usando los settings de QGIS. Idempotente.

        Lanza QgsProcessingException con instrucciones claras si falla
        (NO llama a ee.Authenticate() interactivamente).
        """
        # Import diferido. `...utils` sube 3 niveles:
        #   gee_session.py -> session -> core -> paquete raíz
        from ...utils.gee_init import ensure_gee_initialized
        ensure_gee_initialized(feedback=feedback, force=force)

    @property
    def initialized(self) -> bool:
        from ...utils.gee_init import is_initialized
        return is_initialized()
