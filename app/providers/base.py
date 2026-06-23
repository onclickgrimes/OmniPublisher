from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseProvider(ABC):
    """
    Classe base para todos os provedores de redes sociais.
    Padroniza a interface que o Orquestrador vai usar.
    """
    
    @abstractmethod
    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        """
        Faz o upload do vídeo.
        
        Args:
            video_path (str): Caminho absoluto para o arquivo .mp4 local.
            caption (str): Legenda do vídeo.
            **kwargs: Parâmetros adicionais específicos da plataforma, como thumb_path.
            
        Returns:
            Dict[str, Any]: Dicionário com informações de sucesso/falha.
        """
        pass

    @abstractmethod
    async def validate_session(self) -> bool:
        """
        Verifica se a sessão/token atual é válida.
        
        Returns:
            bool: True se a sessão for válida, False caso contrário.
        """
        pass
