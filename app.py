# No monkey_patching is needed for gevent in this setup
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import random
import os

# Create the Flask app instance first
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a-simple-and-working-secret-key-finally'

# Initialize SocketIO with gevent as the async_mode and attach it to the app
socketio = SocketIO(app, async_mode='gevent')

# --- Constants ---
SECRET_COLORS = ["red", "blue", "green", "yellow", "black", "white"]
GUESS_OPTIONS = SECRET_COLORS + ["empty"]
CODE_LENGTH = 5
NUM_COLOR_PEGS = 4

# --- Game State Class ---
class GameState:
    def __init__(self):
        self.players = {}
        self.game_started = False
        self.player_order = []
        self.current_turn_sid = None
        self.host_sid = None
        self.guesses = []
        self.secret_code = []
        print("--- New, Clean GameState created. Server is ready. ---")

    def get_player_list_data(self):
        # FIX for [object Object]: Send a list of objects, not strings.
        return [{"name": data["name"]} for data in self.players.values()]

    def reset_board(self):
        for player_data in self.players.values():
            player_data.pop("secret", None)
            player_data.pop("eliminated", None)
        self.game_started = False
        self.current_turn_sid = None
        self.player_order = []
        self.guesses = []
        self.secret_code = []
        print("--- Game board has been reset. ---")

GAME = GameState()

@app.route('/')
def index():
    return render_template('index.html')

# STABILITY FIX: Handler for the client's keep-alive ping.
@socketio.on('client_ping')
def handle_client_ping():
    # This function intentionally does nothing. Its only job is to receive an event
    # to keep the Render service from going idle.
    pass

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    emit('color_list', {'colors': GUESS_OPTIONS})
    if GAME.game_started:
        emit('game_in_progress')

@socketio.on('disconnect')
def handle_disconnect():
    global GAME
    if request.sid in GAME.players:
        player_name = GAME.players.pop(request.sid).get("name", "A player")
        print(f"Player '{player_name}' disconnected.")
        if request.sid == GAME.host_sid:
            print("Host disconnected. Full server reset.")
            GAME = GameState()
            emit('game_reset_full', {'message': 'The Host has disconnected. The game has been fully reset.'}, broadcast=True)
        else:
            if GAME.game_started:
                GAME.reset_board()
                emit('game_reset_board', {'message': f'{player_name} left. The game board has been reset.'}, broadcast=True)
            emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

@socketio.on('register_player')
def handle_register(data):
    if GAME.game_started: return
    sid = request.sid
    name = data.get('name', f'Player_{sid[:4]}')
    is_host = not GAME.host_sid
    if is_host:
        GAME.host_sid = sid; name += " (Host)"
    GAME.players[sid] = {"name": name, "is_host": is_host}
    emit('is_host', {'is_host': is_host})
    emit('update_player_list', {'players': GAME.get_player_list_data()}, broadcast=True)

# ... (All other game logic functions like start_game, submit_guess are correct) ...
@socketio.on('start_game')
def handle_start_game():
    if request.sid != GAME.host_sid or GAME.game_started: return
    actual_players_sids = [sid for sid, p_data in GAME.players.items() if not p_data["is_host"]]
    if len(actual_players_sids) < 2: return emit('error', {'message': 'Need at least 2 players to start.'})
    GAME.game_started = True; GAME.secret_code = random.sample(SECRET_COLORS, NUM_COLOR_PEGS) + ['empty']; random.shuffle(GAME.secret_code)
    color_positions = [i for i, color in enumerate(GAME.secret_code) if color != 'empty']; random.shuffle(color_positions)
    for i, player_sid in enumerate(actual_players_sids):
        if i < len(color_positions):
            pos_index = color_positions[i]; secret = {"pos": pos_index + 1, "color": GAME.secret_code[pos_index]}
            GAME.players[player_sid]["secret"] = secret; emit('your_secret', secret, room=player_sid)
    GAME.player_order = actual_players_sids; random.shuffle(GAME.player_order); GAME.current_turn_sid = GAME.player_order[0]
    current_player_name = GAME.players[GAME.current_turn_sid]["name"]
    emit('host_overview', {'secret_code': GAME.secret_code}, room=GAME.host_sid)
    emit('game_started', {'turn': current_player_name}, broadcast=True)

# The if __name__ == '__main__': block is not needed and should not be present
# when using a Gunicorn server, as Gunicorn handles running the app.
