
from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..services.playlists import pl_load

router = APIRouter()

@router.get("/api/player/state")
def api_player_state(request: Request):
    s = request.app.state.PLAYER.get_state()
    return JSONResponse(s)

@router.post("/player/next")
def api_player_next(request: Request):
    request.app.state.PLAYER.next()
    return PlainTextResponse("ok")

@router.post("/player/prev")
def api_player_prev(request: Request):
    request.app.state.PLAYER.prev()
    return PlainTextResponse("ok")

@router.post("/player/stop")
def api_player_stop(request: Request):
    request.app.state.PLAYER.stop_playlist()
    app = request.app
    app.state.LED.clear((0,0,0))

    if getattr(app.state, "MQTT", None):
        try:
            app.state.MQTT.publish_now_playing(title=None, playlist=None, file_or_token=None)
        except Exception:
            pass
    return PlainTextResponse("ok")

@router.post("/player/start")
def api_player_start(request: Request, plname: str):
    app = request.app

    if getattr(app.state, "MQTT", None):
        def _np(title, playlist, file_or_token):
            try:
                app.state.MQTT.publish_now_playing(title=title, playlist=playlist, file_or_token=file_or_token)
            except Exception:
                pass
        app.state.PLAYER.on_now_playing = _np

    pl = pl_load(plname)
    app.state.PLAYER.load(pl)
    return PlainTextResponse("ok")
