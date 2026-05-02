import sys
import types


def install_import_stubs():
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: False
        sys.modules["dotenv"] = dotenv

    if "openai" not in sys.modules:
        openai = types.ModuleType("openai")

        class OpenAI:
            def __init__(self, *args, **kwargs):
                pass

        openai.OpenAI = OpenAI
        sys.modules["openai"] = openai

    if "fastapi" not in sys.modules:
        fastapi = types.ModuleType("fastapi")

        class Request:
            pass

        fastapi.Request = Request
        sys.modules["fastapi"] = fastapi

    if "requests" not in sys.modules:
        requests = types.ModuleType("requests")

        class Response:
            status_code = 404
            text = ""

            def json(self):
                return {}

        requests.get = lambda *args, **kwargs: Response()
        sys.modules["requests"] = requests

    if "pydantic" not in sys.modules:
        pydantic = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **data):
                for key, value in data.items():
                    setattr(self, key, value)

            def model_dump(self):
                return dict(self.__dict__)

        def Field(default=None, **kwargs):
            return default

        pydantic.BaseModel = BaseModel
        pydantic.Field = Field
        sys.modules["pydantic"] = pydantic

    if "chainlit" not in sys.modules:
        chainlit = types.ModuleType("chainlit")

        def _decorator(*args, **kwargs):
            if args and callable(args[0]) and len(args) == 1 and not kwargs:
                return args[0]

            def _wrap(fn):
                return fn
            return _wrap

        class Message:
            def __init__(self, content="", author=None, actions=None, elements=None, **kwargs):
                self.content = content
                self.author = author
                self.actions = actions or []
                self.elements = elements or []

            async def send(self):
                return None

        class Action:
            def __init__(self, name=None, payload=None, label=None, value=None, **kwargs):
                self.name = name
                self.payload = payload or {}
                self.label = label
                self.value = value

        class File:
            def __init__(self, name=None, content=None, display=None, mime=None, **kwargs):
                self.name = name
                self.content = content
                self.display = display
                self.mime = mime

        chainlit.Message = Message
        chainlit.Action = Action
        chainlit.File = File
        chainlit.user_session = types.SimpleNamespace(get=lambda *a, **k: None, set=lambda *a, **k: None)
        chainlit.context = types.SimpleNamespace(session=None)
        chainlit.on_chat_start = _decorator
        chainlit.on_chat_resume = _decorator
        chainlit.on_message = _decorator
        chainlit.on_stop = _decorator
        chainlit.action_callback = _decorator
        sys.modules["chainlit"] = chainlit

        server = types.ModuleType("chainlit.server")
        server.app = types.SimpleNamespace(middleware=lambda *args, **kwargs: _decorator)
        sys.modules["chainlit.server"] = server
