# sensor_base.py

class BaseEnvSensor:
    """
    Osnovni interfejs za senzore kvaliteta vazduha.
    Svaki konkretan senzor treba da implementira measure_one_minute(...)
    i da vrati dict sa ključevima pm2_5, temperature, humidity... (ako ih ima).
    """

    def get_supported_fields(self) -> list:
        """Vrati listu ključeva koje senzor može da meri."""
        raise NotImplementedError
    
    def measure_one_minute(self,
                           samples_count: int = 5,
                           interval_seconds: int = 1,
                           trim_extremes: bool = True):
        raise NotImplementedError