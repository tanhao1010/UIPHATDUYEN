import os
import sys
import time
import json
import cv2
import queue
import smtplib
import threading
from email.message import EmailMessage
from datetime import datetime
import numpy as np

# Flask imports
try:
    from flask import Flask, Response, jsonify, request, send_from_directory
except ImportError:
    print("[SYSTEM] Flask not found. Please install it using: pip install Flask")
    # We will let the script proceed so it can be installed, or we can run a command later

# YOLO Import
try:
    from ultralytics import YOLO
except ImportError:
    print("[SYSTEM] YOLO not found. Please install it using: pip install ultralytics")

# Hardware Pi 5 GPIO Import Handler
GPIO_MODE = "mock"
try:
    from gpiozero import AngularServo, LED, Buzzer
    GPIO_MODE = "gpiozero"
except ImportError:
    try:
        import RPi.GPIO as GPIO
        GPIO_MODE = "rpi_gpio"
    except ImportError:
        GPIO_MODE = "mock"

# Setup Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
WEBSITE_DIR = os.path.join(BASE_DIR, "website")

# Face Recognition & Serial Port Imports
import re
import unicodedata

FACE_REC_AVAILABLE = False
try:
    import face_recognition
    FACE_REC_AVAILABLE = True
    print(f"[SYSTEM] face_recognition library imported from: {face_recognition.__file__}")
except Exception as _e:
    print(f"[SYSTEM] face_recognition import FAILED ({type(_e).__name__}): {_e}")
    print("[SYSTEM] Face ID will run in simulation mode.")

SERIAL_AVAILABLE = False
try:
    import serial
    SERIAL_AVAILABLE = True
    print("[SYSTEM] pyserial library imported successfully.")
except ImportError:
    print("[SYSTEM] pyserial library not found. SIM A7680C will run in simulation mode.")

# Ensure website directory exists
if not os.path.exists(WEBSITE_DIR):
    os.makedirs(WEBSITE_DIR)

# Ensure known_faces directory exists
KNOWN_FACES_DIR = os.path.join(BASE_DIR, "known_faces")
if not os.path.exists(KNOWN_FACES_DIR):
    os.makedirs(KNOWN_FACES_DIR)

# ----------------- CONFIGURATION MANAGEMENT -----------------
DEFAULT_CONFIG = {
    "sender_email": "phatduyen17@gmail.com",
    "app_password": "momtnvdrzejzurbs",
    "receiver_email": "tanhaonguyen0402@gmail.com",
    "email_alerts_enabled": True,
    "buzzer_alerts_enabled": True,
    "system_armed": True,
    "cooldown_seconds": 30.0,
    "stable_seconds": 2.0,
    "confidence": 0.45,
    "min_box_area": 1500,
    "device_1_name": "Đèn Cổng Chính",
    "device_2_name": "Khóa Cửa Điện",
    "device_3_name": "Quạt Thông Gió",
    "web_username": "admin",
    "web_password": "123456",
    
    # GSM SIM A7680C defaults
    "sms_alerts_enabled": True,
    "call_alerts_enabled": True,
    "alert_phone_number": "0901234567",
    "sms_message_template": "GuardShield AI Canh bao: Phat hien nguoi la xam nhap vao luc {time}!",
    # Pi 5: GPIO14(TX)/GPIO15(RX) -> /dev/serial0 (symlink to /dev/ttyAMA0).
    # Windows fallback: COM3.
    "sim_port": "COM3" if os.name == "nt" else "/dev/serial0",
    "sim_baudrate": 115200,
    
    # Face Recognition defaults
    "face_recognition_enabled": True,
    "face_distance_tolerance": 0.6,
    "ignore_alerts_for_family": True,
    "alarm_on_undetected_faces": False,
    "fall_detection_enabled": True,
    "fall_stable_seconds": 1.5,
    "fall_box_aspect_ratio": 1.25
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
                # Merge with defaults in case of missing keys
                for k, v in DEFAULT_CONFIG.items():
                    if k not in config:
                        config[k] = v
                # Device 2 was briefly assigned to the servo. Keep it as the
                # original GPIO 27 device now that the SG90 has its own control.
                if config.get("device_2_name") == "Khóa Cửa Servo (SG90)":
                    config["device_2_name"] = DEFAULT_CONFIG["device_2_name"]
                    save_config(config)
                return config
        except Exception as e:
            print(f"[CONFIG] Error loading config: {e}. Using defaults.")
    
    # Save default config if not found
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()

def save_config(config_data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
        print("[CONFIG] Configuration saved successfully")
    except Exception as e:
        print(f"[CONFIG] Error saving config: {e}")

system_config = load_config()

# ----------------- HARDWARE GPIO CONTROLLER -----------------
class HardwareController:
    def __init__(self):
        self.mode = GPIO_MODE
        print(f"[HARDWARE] Initializing in '{self.mode}' mode")
        self.devices = {1: None, 2: None, 3: None}
        self.buzzer = None
        self.servo = None
        self.servo_pwm = None
        
        # BCM Pin mappings (Default Pi 5 setup)
        self.pins = {
            1: 17,        # Device 1: GPIO 17
            2: 27,        # Device 2: GPIO 27
            3: 22,        # Device 3: GPIO 22
            "buzzer": 23, # Buzzer: GPIO 23
            "servo": 25   # Door lock servo SG90: GPIO 25
        }
        
        self.states = {
            1: False,
            2: False,
            3: False,
            "servo": False,
            "buzzer": False
        }
        
        if self.mode == "gpiozero":
            try:
                self.devices[1] = LED(self.pins[1])
                self.devices[2] = LED(self.pins[2])
                self.devices[3] = LED(self.pins[3])
                self.buzzer = Buzzer(self.pins["buzzer"])
                # SG90: 0 deg when off, 90 deg when the door lock is opened.
                # The 0.5-2.5 ms pulse range covers the usual SG90 calibration range.
                self.servo = AngularServo(
                    self.pins["servo"],
                    min_angle=0,
                    max_angle=90,
                    min_pulse_width=0.0005,
                    max_pulse_width=0.0025,
                )
                # Reset all to off
                for dev in self.devices.values():
                    dev.off()
                self.buzzer.off()
                self.set_servo(False)
            except Exception as e:
                print(f"[HARDWARE] Gpiozero initialization failed: {e}. Falling back to MOCK.")
                self.mode = "mock"
                
        elif self.mode == "rpi_gpio":
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                for dev_id, pin in self.pins.items():
                    GPIO.setup(pin, GPIO.OUT)
                    GPIO.output(pin, GPIO.LOW)
                self.servo_pwm = GPIO.PWM(self.pins["servo"], 50)
                self.servo_pwm.start(0)
                self.set_servo(False)
            except Exception as e:
                print(f"[HARDWARE] RPi.GPIO initialization failed: {e}. Falling back to MOCK.")
                self.mode = "mock"

    def set_device(self, device_id, state):
        if device_id not in [1, 2, 3]:
            return

        state = bool(state)
        self.states[device_id] = state
        print(f"[HARDWARE] GPIO Control - Device {device_id} ({system_config.get(f'device_{device_id}_name')}) -> {'ON' if state else 'OFF'}")
        
        if self.mode == "gpiozero":
            try:
                if state:
                    self.devices[device_id].on()
                else:
                    self.devices[device_id].off()
            except Exception as e:
                print(f"[HARDWARE] Gpiozero error on device {device_id}: {e}")
                
        elif self.mode == "rpi_gpio":
            try:
                pin = self.pins[device_id]
                GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
            except Exception as e:
                print(f"[HARDWARE] RPi.GPIO error on device {device_id}: {e}")

    def set_servo(self, state):
        """Move the SG90 door-lock servo to 90 deg (open) or 0 deg (closed)."""
        state = bool(state)
        angle = 90 if state else 0
        self.states["servo"] = state
        print(f"[HARDWARE] Servo SG90 GPIO {self.pins['servo']} -> {angle} degrees")

        if self.mode == "gpiozero":
            try:
                self.servo.angle = angle
            except Exception as e:
                print(f"[HARDWARE] Gpiozero error on SG90 servo: {e}")

        elif self.mode == "rpi_gpio":
            try:
                # 50 Hz PWM: 0 deg = 0.5 ms (2.5%), 90 deg = 1.5 ms (7.5%).
                duty_cycle = 2.5 + (angle / 180.0) * 10.0
                self.servo_pwm.ChangeDutyCycle(duty_cycle)
            except Exception as e:
                print(f"[HARDWARE] RPi.GPIO error on SG90 servo: {e}")
                
    def set_buzzer(self, state):
        state = bool(state)
        self.states["buzzer"] = state
        print(f"[HARDWARE] GPIO Control - Buzzer/Siren -> {'ON' if state else 'OFF'}")
        
        if self.mode == "gpiozero":
            try:
                if state:
                    self.buzzer.on()
                else:
                    self.buzzer.off()
            except Exception as e:
                print(f"[HARDWARE] Gpiozero error on buzzer: {e}")
                
        elif self.mode == "rpi_gpio":
            try:
                pin = self.pins["buzzer"]
                GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)
            except Exception as e:
                print(f"[HARDWARE] RPi.GPIO error on buzzer: {e}")

    def cleanup(self):
        print("[HARDWARE] Cleaning up GPIO connections...")
        # Return the lock to the closed position before releasing the GPIO pin.
        self.set_servo(False)
        time.sleep(0.4)
        if self.mode == "gpiozero" and self.servo is not None:
            try:
                self.servo.detach()
            except Exception as e:
                print(f"[HARDWARE] Servo detach error: {e}")
        if self.mode == "rpi_gpio":
            try:
                if self.servo_pwm is not None:
                    self.servo_pwm.stop()
                GPIO.cleanup()
            except Exception as e:
                print(f"[HARDWARE] GPIO cleanup error: {e}")

hw_controller = HardwareController()

# ----------------- GLOBALS & SHARED VARIABLES -----------------
raw_frame = None                # Unannotated frame shared for YOLO
encoded_jpeg_frame = None       # Pre-encoded annotated JPEG byte array
detected_boxes = []             # Decoupled bounding boxes updated by YOLO
detection_fps = 0.0
is_person_detected = False
email_sending = False
last_email_time = 0.0
last_gsm_time = 0.0
last_alert_trigger_time = 0.0
active_alert_timer = None       # Auto shutoff buzzer timer
event_logs = []                 # Store security event history in-memory

def add_event(description, category="info"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_item = {
        "timestamp": timestamp,
        "description": description,
        "category": category
    }
    event_logs.insert(0, log_item)
    # Keep only recent 100 logs
    if len(event_logs) > 100:
        event_logs.pop()
    print(f"[EVENT LOG] {timestamp} - {description}")

add_event("Hệ thống khởi động thành công. Sẵn sàng giám sát.", "success")

# ----------------- VIETNAMESE ACCENT REMOVER -----------------
def remove_vietnamese_accents(text):
    patterns = {
        '[àáảãạăằắẳẵặâầấẩẫậ]': 'a',
        '[ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬ]': 'A',
        '[èéẻẽẹêềếểễệ]': 'e',
        '[ÈÉẺẼẸÊỀẾỂỄỆ]': 'E',
        '[ìíỉĩị]': 'i',
        '[ÌÍỈĨỊ]': 'I',
        '[òóỏõọôồốổỗộơờớởỡợ]': 'o',
        '[ÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ]': 'O',
        '[ùúủũụưừứửữự]': 'u',
        '[ÙÚỦŨỤƯỪỨỬỮỰ]': 'U',
        '[ỳýỷỹỵ]': 'y',
        '[ỲÝỶỸỴ]': 'Y',
        'đ': 'd',
        'Đ': 'D'
    }
    for pattern, replacement in patterns.items():
        text = re.sub(pattern, replacement, text)
    return text

# ----------------- FACE ID MANAGER -----------------
class FaceIDManager:
    def __init__(self):
        self.known_face_encodings = []
        self.known_face_names = []
        self.lock = threading.Lock()
        self.load_known_faces()

    def load_known_faces(self):
        with self.lock:
            self.known_face_encodings = []
            self.known_face_names = []
            
            if not FACE_REC_AVAILABLE:
                print("[FACE ID] Library not available. Face ID will run in simulation/mock mode.")
                return

            print("[FACE ID] Loading known faces from directory...")
            try:
                for filename in os.listdir(KNOWN_FACES_DIR):
                    if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                        name = os.path.splitext(filename)[0].replace('_', ' ')
                        filepath = os.path.join(KNOWN_FACES_DIR, filename)
                        try:
                            image = face_recognition.load_image_file(filepath)
                            encodings = face_recognition.face_encodings(image)
                            if len(encodings) > 0:
                                self.known_face_encodings.append(encodings[0])
                                self.known_face_names.append(name)
                                print(f"[FACE ID] Loaded: {name} ({filename})")
                            else:
                                print(f"[FACE ID] Warning: No face found in {filename}")
                        except Exception as e:
                            print(f"[FACE ID] Error loading face {filename}: {e}")
            except Exception as e:
                print(f"[FACE ID] Error scanning known_faces directory: {e}")
            print(f"[FACE ID] Loaded total {len(self.known_face_names)} known faces.")

    def add_face(self, name, image_file_path):
        # Normalizing name for safe filename
        self.load_known_faces()
        return True

    def delete_face(self, name):
        # Apply the same name-normalization used at save time so deletion always matches
        ascii_name = remove_vietnamese_accents(name)
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', ascii_name).strip('_')
        filepath = None
        # Try multiple extensions in case the file was saved as PNG instead of JPG
        for ext in ('.jpg', '.jpeg', '.png'):
            candidate = os.path.join(KNOWN_FACES_DIR, f"{safe_name}{ext}")
            if os.path.exists(candidate):
                filepath = candidate
                break

        if filepath:
            try:
                os.remove(filepath)
                print(f"[FACE ID] Deleted face: {name} at {filepath}")
                self.load_known_faces()
                return True
            except Exception as e:
                print(f"[FACE ID] Error deleting face file: {e}")
                return False
        else:
            print(f"[FACE ID] Face file not found to delete: {name}")
            return False

    def list_faces(self):
        faces = []
        if os.path.exists(KNOWN_FACES_DIR):
            for filename in os.listdir(KNOWN_FACES_DIR):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    name = os.path.splitext(filename)[0].replace('_', ' ')
                    faces.append(name)
        return faces

    def identify_face_in_crop(self, crop_image, tolerance=0.6):
        if not FACE_REC_AVAILABLE or not self.known_face_encodings:
            return None # Face Recognition unavailable or database empty

        try:
            h, w = crop_image.shape[:2]
            # Skip crops that are too small for reliable face detection on Pi 5
            if h < 80 or w < 60:
                return "no_face"

            # Keep enough detail for the face inside a full-person YOLO crop.
            TARGET_H = 360
            if h > TARGET_H:
                scale = TARGET_H / float(h)
                new_w = max(1, int(w * scale))
                crop_image = cv2.resize(crop_image, (new_w, TARGET_H), interpolation=cv2.INTER_AREA)
            elif h < 220:
                scale = 220 / float(h)
                new_w = max(1, int(w * scale))
                crop_image = cv2.resize(crop_image, (new_w, 220), interpolation=cv2.INTER_CUBIC)

            rgb_crop = cv2.cvtColor(crop_image, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_crop, model="hog", number_of_times_to_upsample=1)
            if not face_locations:
                return "no_face"

            encodings = face_recognition.face_encodings(rgb_crop, face_locations, num_jitters=2)
            if not encodings:
                return "no_face"

            matches = face_recognition.compare_faces(self.known_face_encodings, encodings[0], tolerance=tolerance)
            face_distances = face_recognition.face_distance(self.known_face_encodings, encodings[0])

            if True in matches:
                best_match_index = int(np.argmin(face_distances))
                if matches[best_match_index]:
                    return self.known_face_names[best_match_index]

            return "stranger"
        except Exception as e:
            print(f"[FACE ID] Error matching face: {e}")
            return None

# ----------------- SIM A7680C GSM CONTROLLER -----------------
class GSMController:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.port = system_config.get("sim_port", "/dev/ttyS0")
        self.baudrate = system_config.get("sim_baudrate", 115200)
        self.active_call_thread = None
        self.call_running = False
        self.init_serial()

    def init_serial(self):
        if not SERIAL_AVAILABLE:
            print("[GSM] Serial library not found. Running in MOCK mode.")
            return False
            
        self.port = system_config.get("sim_port", "/dev/ttyS0")
        self.baudrate = system_config.get("sim_baudrate", 115200)
        
        try:
            print(f"[GSM] Opening serial port {self.port} at {self.baudrate} baud...")
            self.ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=3)
            print("[GSM] Serial port opened successfully.")
            return True
        except Exception as e:
            print(f"[GSM] Serial port opening failed: {e}. Falling back to MOCK mode.")
            self.ser = None
            return False

    def send_at_command(self, cmd, delay=0.5):
        if not self.ser:
            return ""
        try:
            # Clear input buffer
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\r\n").encode())
            time.sleep(delay)
            response = self.ser.read_all().decode(errors='ignore')
            print(f"[GSM AT] Command: {cmd} -> Response: {response.strip()}")
            return response
        except Exception as e:
            print(f"[GSM AT] Error sending command {cmd}: {e}")
            return ""

    def send_sms(self, phone_number, message_text):
        normalized_msg = remove_vietnamese_accents(message_text)
        print(f"[GSM] Outbox SMS to {phone_number}: {normalized_msg}")
        
        add_event(f"[GSM SMS] Đang gửi tin nhắn tới {phone_number}...", "info")
        
        if not self.ser:
            # Simulator mode
            add_event(f"[GSM SMS MOCK] Gửi SMS thành công tới {phone_number} (Chế độ Giả lập)", "success")
            return True
            
        with self.lock:
            try:
                # 1. Set text mode
                res = self.send_at_command("AT+CMGF=1", 0.5)
                if "OK" not in res:
                    print("[GSM SMS] Failed to set text mode. Re-initializing...")
                    self.init_serial()
                    res = self.send_at_command("AT+CMGF=1", 0.5)
                    
                # 2. Set recipient
                cmd = f'AT+CMGS="{phone_number}"'
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\r\n").encode())
                time.sleep(0.5)
                
                # Check for prompt '>'
                res = self.ser.read_all().decode(errors='ignore')
                if ">" not in res:
                    print(f"[GSM SMS] Recipient set failed. Response: {res}")
                
                # 3. Write message text and Send (Ctrl+Z = \x1a)
                self.ser.write((normalized_msg + "\x1a").encode())
                time.sleep(4) # Wait for SMS center confirmation
                res = self.ser.read_all().decode(errors='ignore')
                
                if "OK" in res or "+CMGS:" in res:
                    add_event(f"[GSM SMS] Đã gửi SMS cảnh báo tới {phone_number} thành công!", "success")
                    return True
                else:
                    add_event(f"[GSM SMS] Gửi tin nhắn SMS thất bại. Phản hồi SIM: {res.strip()}", "danger")
                    return False
            except Exception as e:
                add_event(f"[GSM SMS] Lỗi kết nối Serial SIM: {str(e)}", "danger")
                return False

    def make_call(self, phone_number):
        if self.call_running:
            print("[GSM CALL] Call already in progress, skipping.")
            return False
            
        add_event(f"[GSM Call] Đang thực hiện cuộc gọi khẩn cấp tới {phone_number}...", "warning")
        
        if not self.ser:
            # Simulator mode
            add_event(f"[GSM Call MOCK] Đang gọi điện tới {phone_number} (Chế độ Giả lập)...", "warning")
            self.call_running = True
            def mock_call_thread():
                time.sleep(15)
                self.call_running = False
                add_event(f"[GSM Call MOCK] Kết thúc cuộc gọi giả lập với {phone_number}", "info")
            threading.Thread(target=mock_call_thread, daemon=True).start()
            return True
            
        self.call_running = True
        self.active_call_thread = threading.Thread(target=self._call_execution_thread, args=(phone_number,), daemon=True)
        self.active_call_thread.start()
        return True

    def _call_execution_thread(self, phone_number):
        with self.lock:
            try:
                # 1. Dial number
                res = self.send_at_command(f"ATD{phone_number};", 1.0)
                if "OK" in res or "NO CARRIER" not in res:
                    add_event(f"[GSM Call] Cuộc gọi đang đổ chuông tới {phone_number}. Đang chờ 15s để báo hiệu...", "success")
                else:
                    add_event(f"[GSM Call] Gọi điện thất bại. Phản hồi SIM: {res.strip()}", "danger")
                    self.call_running = False
                    return
            except Exception as e:
                add_event(f"[GSM Call] Lỗi thực hiện gọi điện: {str(e)}", "danger")
                self.call_running = False
                return
                
        # 2. Wait 15 seconds while phone rings
        time.sleep(15)
        
        # 3. Hang up
        with self.lock:
            try:
                self.send_at_command("ATH", 0.5)
                add_event(f"[GSM Call] Đã gác máy cuộc gọi khẩn cấp với {phone_number}.", "info")
            except Exception as e:
                print(f"[GSM CALL] Error hanging up: {e}")
            finally:
                self.call_running = False

# Instantiate face recognition and GSM controller
face_id_manager = FaceIDManager()
gsm_controller = GSMController()

# ----------------- EMAIL SENDING THREAD -----------------
def send_email_alert(image_path):
    global email_sending, last_email_time
    try:
        email_sending = True
        add_event("Đang chuẩn bị gửi email cảnh báo...", "info")
        
        sender = system_config["sender_email"]
        password = system_config["app_password"]
        receiver = system_config["receiver_email"]
        
        msg = EmailMessage()
        msg["Subject"] = "🚨 CẢNH BÁO: Phát hiện xâm nhập trái phép! 🚨"
        msg["From"] = sender
        msg["To"] = receiver
        
        body = f"""Hệ thống Giám sát An ninh GuardShield AI™ đã phát hiện có người xuất hiện trong khu vực giám sát.
        
Thời gian phát hiện: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
Trạng thái: Báo động còi hú đã được kích hoạt.
        
Chi tiết hình ảnh camera ghi nhận được gửi kèm trong thư này."""
        
        msg.set_content(body)
        
        # Attach image
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                img_data = f.read()
                msg.add_attachment(
                    img_data,
                    maintype="image",
                    subtype="jpeg",
                    filename="guardshield_snapshot.jpg"
                )
        
        # Send SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
            
        last_email_time = time.time()
        add_event(f"Đã gửi email cảnh báo xâm nhập thành công tới {receiver}", "success")
    except Exception as e:
        add_event(f"Lỗi gửi email cảnh báo: {str(e)}", "danger")
    finally:
        email_sending = False

# ----------------- CAMERA & YOLO DETECTION PIPELINE -----------------
class CameraDetector:
    # Tuning constants for the 2-stage YOLO -> Face Recognition pipeline on Pi 5
    TRACK_IOU_THRESHOLD = 0.3       # min IoU to consider boxes the same track
    TRACK_STALE_SECONDS = 30.0      # forget tracks not seen for this long (out-of-frame tolerance)
    FACE_CACHE_VALIDITY = 60.0      # cache lives as long as the track does (effectively unlimited)
    FACE_REVERIFY_INTERVAL = 8.0    # only re-verify uncertain results (stranger/no_face), never family
    FACE_PENDING_TIMEOUT = 6.0      # give Face ID more time before treating the person as unknown
    FACE_SUBMIT_COOLDOWN = 0.7      # don't spam the worker queue for the same track

    def __init__(self):
        self.model = None
        self.running = False
        self.cam = None
        self.picam2 = None
        self.mode = "simulation" # 'picamera2', 'webcam', 'simulation'

        # 2-stage pipeline state (light YOLO -> async face recognition worker)
        self.tracks = {}            # track_id -> {"box": (x1,y1,x2,y2), "last_seen": ts}
        self.next_track_id = 1
        self.pending_since = {}     # track_id -> first-submit timestamp (YOLO thread only)
        self.last_submit = {}       # track_id -> last queue.put timestamp (YOLO thread only)

        self.face_queue = queue.Queue(maxsize=2)  # bounded -> old crops dropped under load
        self.face_results = {}      # track_id -> {"raw_result", "raw_name", "timestamp"}
        self.face_results_lock = threading.Lock()

    @staticmethod
    def _compute_iou(box_a, box_b):
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
        iw = max(0, ix2 - ix1); ih = max(0, iy2 - iy1)
        inter = iw * ih
        if inter == 0:
            return 0.0
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def _assign_track(self, box, now):
        """Match a new box to an existing track by IoU, or create a new track id."""
        best_id, best_iou = None, self.TRACK_IOU_THRESHOLD
        for tid, tdata in self.tracks.items():
            iou = self._compute_iou(box, tdata["box"])
            if iou > best_iou:
                best_iou = iou
                best_id = tid
        if best_id is None:
            best_id = self.next_track_id
            self.next_track_id += 1
        self.tracks[best_id] = {"box": box, "last_seen": now}
        return best_id

    def _cleanup_stale_tracks(self, now):
        stale = [tid for tid, t in self.tracks.items()
                 if (now - t["last_seen"]) > self.TRACK_STALE_SECONDS]
        for tid in stale:
            self.tracks.pop(tid, None)
            self.pending_since.pop(tid, None)
            self.last_submit.pop(tid, None)
        if stale:
            with self.face_results_lock:
                for tid in stale:
                    self.face_results.pop(tid, None)

    @staticmethod
    def _expanded_crop(frame, box):
        """Crop the upper body with padding so face_recognition gets more face pixels."""
        x1, y1, x2, y2 = box
        frame_h, frame_w = frame.shape[:2]
        box_w = max(1, x2 - x1)
        box_h = max(1, y2 - y1)
        pad_x = int(box_w * 0.25)
        pad_top = int(box_h * 0.12)
        upper_h = int(box_h * 0.68)
        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_top)
        cx2 = min(frame_w, x2 + pad_x)
        cy2 = min(frame_h, y1 + upper_h)
        return frame[cy1:cy2, cx1:cx2]

    @staticmethod
    def _looks_like_fall(box, frame_shape):
        x1, y1, x2, y2 = box
        frame_h, frame_w = frame_shape[:2]
        box_w = max(1, x2 - x1)
        box_h = max(1, y2 - y1)
        aspect = box_w / float(box_h)
        center_y = (y1 + y2) / 2.0
        width_ratio = box_w / float(max(1, frame_w))
        height_ratio = box_h / float(max(1, frame_h))
        min_aspect = float(system_config.get("fall_box_aspect_ratio", 1.25))
        return (
            system_config.get("fall_detection_enabled", True)
            and aspect >= min_aspect
            and center_y > frame_h * 0.42
            and width_ratio > 0.18
            and height_ratio < 0.65
        )

    def _face_worker(self):
        """Background thread: runs heavy face_recognition off the YOLO loop."""
        print("[FACE WORKER] Async face recognition worker started.")
        while self.running:
            try:
                item = self.face_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            track_id, crop, tolerance = item
            try:
                result = face_id_manager.identify_face_in_crop(crop, tolerance)
            except Exception as e:
                print(f"[FACE WORKER] Recognition error: {e}")
                result = None

            if result and result not in ("no_face", "stranger"):
                payload = {"raw_result": "family", "raw_name": result}
            elif result == "no_face":
                payload = {"raw_result": "no_face", "raw_name": None}
            elif result == "stranger":
                payload = {"raw_result": "stranger", "raw_name": None}
            else:
                payload = {"raw_result": "error", "raw_name": None}
            payload["timestamp"] = time.time()

            with self.face_results_lock:
                self.face_results[track_id] = payload
        print("[FACE WORKER] Stopped.")

    def _resolve_label_from_cache(self, cached):
        """Translate a cached raw result + current config into (label, type, color)."""
        raw = cached.get("raw_result")
        name = cached.get("raw_name")
        if raw == "family":
            return (f"Nguoi nha: {name}", "family", (254, 242, 0))
        if raw == "no_face":
            if system_config.get("alarm_on_undetected_faces", True):
                return ("Nguoi la (Khong ro mat)", "stranger", (54, 51, 255))
            return ("Nguoi (Chua ro danh tinh)", "unknown", (0, 165, 255))
        if raw == "stranger":
            return ("Nguoi la", "stranger", (54, 51, 255))
        return ("Nguoi", "unknown", (0, 165, 255))

    @staticmethod
    def _alert_message(alert_reason):
        if alert_reason == "fall":
            return "CANH BAO TE NGA! Da kich hoat coi/canh bao khan cap."
        return "CANH BAO DOT NHAP! Da kich hoat coi/canh bao khan cap."

    def init_camera(self):
        # 1. Try Picamera2 (Pi CSI Camera)
        try:
            from picamera2 import Picamera2
            print("[CAMERA] Picamera2 library found, attempting initialization...")
            self.picam2 = Picamera2()
            config = self.picam2.create_preview_configuration(
                main={"size": (1280, 720), "format": "RGB888"},
                lores={"size": (320, 240), "format": "RGB888"},
                display="lores"
            )
            self.picam2.configure(config)
            self.picam2.start()
            self.mode = "picamera2"
            print("[CAMERA] Picamera2 initialized successfully!")
            return True
        except Exception as e:
            print(f"[CAMERA] Picamera2 init failed or not on Raspberry Pi: {e}")
            
        # 2. Try Standard OpenCV Webcam (USB)
        try:
            print("[CAMERA] Attempting standard webcam initialization via OpenCV...")
            self.cam = cv2.VideoCapture(0)
            if self.cam.isOpened():
                self.mode = "webcam"
                self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                print("[CAMERA] USB Webcam (OpenCV) initialized successfully!")
                return True
            else:
                self.cam = None
                print("[CAMERA] No USB Webcams could be opened")
        except Exception as e:
            print(f"[CAMERA] Webcam init failed: {e}")
            
        # 3. Fallback to Simulation
        self.mode = "simulation"
        print("[CAMERA] CAMERA FALLBACK: Running in Simulation Mode. Generating synthetic security frames.")
        return True

    def init_model(self):
        try:
            print("[YOLO] Loading YOLOv8 nano model...")
            self.model = YOLO("yolov8n.pt")
            print("[YOLO] YOLOv8 loaded successfully!")
        except Exception as e:
            print(f"[YOLO] Error loading YOLO model: {e}. Detection will be simulated.")

    def run_capture(self):
        global encoded_jpeg_frame, raw_frame, detected_boxes, is_person_detected, detection_fps
        
        self.init_camera()
        self.running = True
        
        sim_person_x = 50
        sim_person_y = 150
        sim_person_dir = 1
        
        prev_time = time.time()
        fps_camera = 0.0
        
        print("[CAMERA THREAD] Camera Capture loop started.")
        
        while self.running:
            start_loop = time.time()
            frame_raw = None
            
            # --- 1. CAPTURE FRAME ---
            if self.mode == "picamera2":
                try:
                    frame_raw = self.picam2.capture_array("main")
                    frame_raw = cv2.cvtColor(frame_raw, cv2.COLOR_RGB2BGR)
                except Exception as e:
                    print(f"[CAMERA] Picamera2 read error: {e}. Switching to Simulation.")
                    self.mode = "simulation"
                    
            elif self.mode == "webcam":
                try:
                    ret, frame_raw = self.cam.read()
                    if not ret or frame_raw is None:
                        raise Exception("Failed to read webcam frame")
                except Exception as e:
                    print(f"[CAMERA] USB Webcam read error: {e}. Switching to Simulation.")
                    self.mode = "simulation"
                    
            if self.mode == "simulation":
                frame_raw = np.zeros((480, 640, 3), dtype="uint8")
                
                # Make simulated canvas more complex & interactive
                cv2.rectangle(frame_raw, (0, 0), (640, 480), (18, 12, 8), -1) # Sleek cyber dark base
                
                # Draw technical crosshair/grid lines
                cv2.line(frame_raw, (40, 240), (600, 240), (40, 30, 20), 1)
                cv2.line(frame_raw, (320, 40), (320, 440), (40, 30, 20), 1)
                cv2.circle(frame_raw, (320, 240), 100, (40, 30, 20), 1)
                cv2.circle(frame_raw, (320, 240), 5, (0, 242, 254), -1) # glowing cyan center point
                
                # Draw border HUD corners
                HUD_COLOR = (120, 80, 30) # High-tech blue HUD
                # Top Left
                cv2.line(frame_raw, (10, 10), (40, 10), HUD_COLOR, 2)
                cv2.line(frame_raw, (10, 10), (10, 40), HUD_COLOR, 2)
                # Top Right
                cv2.line(frame_raw, (630, 10), (600, 10), HUD_COLOR, 2)
                cv2.line(frame_raw, (630, 10), (630, 40), HUD_COLOR, 2)
                # Bottom Left
                cv2.line(frame_raw, (10, 470), (40, 470), HUD_COLOR, 2)
                cv2.line(frame_raw, (10, 470), (10, 440), HUD_COLOR, 2)
                # Bottom Right
                cv2.line(frame_raw, (630, 470), (600, 470), HUD_COLOR, 2)
                cv2.line(frame_raw, (630, 470), (630, 440), HUD_COLOR, 2)
                
                # Draw UI Text
                cv2.putText(frame_raw, "SIMULATION NODE - GUARDSHIELD AI", (20, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 242, 254), 1)
                cv2.putText(frame_raw, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), (430, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 242, 254), 1)
                
                # Simulate a moving person if System is ARMED or on demand
                sim_person_x += 5 * sim_person_dir
                if sim_person_x > 450:
                    sim_person_dir = -1
                elif sim_person_x < 50:
                    sim_person_dir = 1
                
                # Let's run a continuous simulation: intruder appears for 15 seconds, disappears for 15 seconds
                curr_second = int(time.time()) % 30
                simulated_intrusion = curr_second < 15
                
                if not system_config["system_armed"]:
                    simulated_intrusion = False
                    
                mock_boxes = []
                if simulated_intrusion:
                    # Draw a mock target intruder box
                    x1, y1 = sim_person_x, sim_person_y
                    x2, y2 = x1 + 120, y1 + 240
                    
                    # Alternate between stranger and family member for PC demonstration
                    is_family = (8 <= curr_second < 15)
                    if is_family:
                        label = "Nguoi nha: Phat Duyen"
                        box_type = "family"
                        box_color = (254, 242, 0) # Cyan
                    else:
                        label = "Nguoi la"
                        box_type = "stranger"
                        box_color = (54, 51, 255) # Red
                        
                    mock_boxes.append({
                        "box": (x1, y1, x2, y2),
                        "label": label,
                        "type": box_type,
                        "color": box_color
                    })
                    
                    # Draw visual simulated humanoid box in high-res frame
                    cv2.rectangle(frame_raw, (x1, y1), (x2, y2), box_color, 2)
                    
                    # Simulated overlay lines pointing to target
                    cv2.line(frame_raw, (320, 240), (int((x1+x2)/2), int((y1+y2)/2)), (0, 165, 255), 1)
                
                detected_boxes = mock_boxes
                is_person_detected = len(mock_boxes) > 0
                
            raw_frame = frame_raw.copy()
            
            # --- 2. RENDER OVERLAYS & DETECTION BOXES ---
            display_frame = frame_raw.copy()
            
            # Use local reference to avoid list-size changes during iteration
            local_boxes = list(detected_boxes)
            for box_info in local_boxes:
                # Support both dict format and old list of tuples (for safety)
                if isinstance(box_info, dict):
                    x1, y1, x2, y2 = box_info["box"]
                    label = box_info["label"]
                    color = box_info["color"]
                else:
                    x1, y1, x2, y2 = box_info
                    label = "Nguoi la"
                    color = (54, 51, 255)
                    
                # Draw neon alarm rectangle
                cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, 2)
                # Draw corner brackets on targets for high-tech aesthetic
                offset = 15
                # Top-left corner
                cv2.line(display_frame, (x1, y1), (x1 + offset, y1), color, 2)
                cv2.line(display_frame, (x1, y1), (x1, y1 + offset), color, 2)
                # Top-right corner
                cv2.line(display_frame, (x2, y1), (x2 - offset, y1), color, 2)
                cv2.line(display_frame, (x2, y1), (x2, y1 + offset), color, 2)
                # Bottom-left corner
                cv2.line(display_frame, (x1, y2), (x1 + offset, y2), color, 2)
                cv2.line(display_frame, (x1, y2), (x1, y2 - offset), color, 2)
                # Bottom-right corner
                cv2.line(display_frame, (x2, y2), (x2 - offset, y2), color, 2)
                cv2.line(display_frame, (x2, y2), (x2, y2 - offset), color, 2)
                
                # Draw label text above bounding box
                cv2.putText(display_frame, label, (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                
            # Clean view without HUD overlays (as per request)
            now = time.time()
            fps_camera = 1 / (now - prev_time)
            prev_time = now
            
            # --- 3. JPEG COMPRESSION (ONCE IN BACKGROUND) ---
            ret, buffer = cv2.imencode('.jpg', display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ret:
                encoded_jpeg_frame = buffer.tobytes()
            
            # Control frame rate loop to match butter-smooth 30fps
            sleep_time = 0.033 - (time.time() - start_loop)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def run_inference(self):
        global raw_frame, detected_boxes, is_person_detected, detection_fps, last_email_time, last_gsm_time
        
        self.init_model()
        
        # Optimize PyTorch core threading to utilize 4 threads (max CPU on Pi 5)
        try:
            import torch
            torch.set_num_threads(4)
            print("[YOLO THREAD] PyTorch successfully configured to use 4 threads.")
        except Exception as e:
            print(f"[YOLO THREAD] PyTorch thread optimization skipped: {e}")
            
        consecutive_detections = 0
        first_detect_time = None
        last_seen_time = None
        fall_detections = 0
        first_fall_time = None
        last_fall_time = None
        
        prev_time = time.time()
        print("[YOLO THREAD] Async YOLO Inference loop started.")
        
        while self.running:
            start_loop = time.time()
            
            # In simulation, the mock bounding box is generated at capture level
            if self.mode == "simulation":
                time.sleep(0.1)
                continue
                
            if raw_frame is None:
                time.sleep(0.02)
                continue
                
            # Perform a fast copy of raw frame & resize for AI detection speedup
            frame_local = raw_frame.copy()
            frame_detect = cv2.resize(frame_local, (256, 256))
            
            detected = []
            should_alarm = False
            alert_reason = None
            family_safe_seen = False
            
            if self.model is not None:
                try:
                    results = self.model(
                        frame_detect,
                        classes=[0], # Person class
                        imgsz=256,
                        conf=system_config["confidence"],
                        device="cpu",
                        verbose=False
                    )
                    
                    # Scale boxes back to frame_local dimensions
                    scale_x = frame_local.shape[1] / 256.0
                    scale_y = frame_local.shape[0] / 256.0
                    
                    now_loop = time.time()
                    fr_enabled = system_config.get("face_recognition_enabled", True)
                    fr_tolerance = system_config.get("face_distance_tolerance", 0.6)

                    for box in results[0].boxes:
                        bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                        x1 = int(bx1 * scale_x)
                        y1 = int(by1 * scale_y)
                        x2 = int(bx2 * scale_x)
                        y2 = int(by2 * scale_y)

                        area = (x2 - x1) * (y2 - y1)
                        if area < system_config["min_box_area"]:
                            continue

                        # Stage 1: track this box by IoU so face recog can be cached per person
                        track_id = self._assign_track((x1, y1, x2, y2), now_loop)

                        # Stage 2: pull cached label from face worker (non-blocking)
                        with self.face_results_lock:
                            cached = self.face_results.get(track_id)
                            cache_copy = dict(cached) if cached else None

                        if not fr_enabled:
                            # Face recognition disabled -> behave like original default
                            label, box_type, box_color = "Nguoi la", "stranger", (54, 51, 255)
                        elif cache_copy and cache_copy.get("raw_result") == "family":
                            label, box_type, box_color = self._resolve_label_from_cache(cache_copy)
                        elif cache_copy and (now_loop - cache_copy["timestamp"]) < self.FACE_CACHE_VALIDITY:
                            label, box_type, box_color = self._resolve_label_from_cache(cache_copy)
                        else:
                            # No verified result yet for this track -> pending
                            first_seen = self.pending_since.setdefault(track_id, now_loop)
                            if (now_loop - first_seen) > self.FACE_PENDING_TIMEOUT:
                                # Fallback after timeout: treat as stranger if config wants alerts on unknowns
                                if system_config.get("alarm_on_undetected_faces", True):
                                    label, box_type, box_color = "Nguoi la (Cho xac dinh)", "stranger", (54, 51, 255)
                                else:
                                    label, box_type, box_color = "Nguoi (Chua ro danh tinh)", "unknown", (0, 165, 255)
                            else:
                                label, box_type, box_color = "Dang nhan dien...", "pending", (0, 165, 255)

                        # Stage 3: decide whether to (re-)submit this track to the face worker
                        # Rule: once a track is confirmed "family", LOCK IT IN — never re-verify.
                        # Only re-verify uncertain results (stranger / no_face) in case lighting / angle improves.
                        should_submit = False
                        if fr_enabled:
                            if cache_copy is None:
                                last = self.last_submit.get(track_id, 0.0)
                                if (now_loop - last) > self.FACE_SUBMIT_COOLDOWN:
                                    should_submit = True
                            elif cache_copy.get("raw_result") == "family":
                                should_submit = False  # locked
                            elif (now_loop - cache_copy["timestamp"]) > self.FACE_REVERIFY_INTERVAL:
                                should_submit = True

                        if should_submit:
                            crop = self._expanded_crop(frame_local, (x1, y1, x2, y2))
                            if crop.size > 0 and crop.shape[0] >= 80 and crop.shape[1] >= 60:
                                try:
                                    self.face_queue.put_nowait((track_id, crop.copy(), fr_tolerance))
                                    self.last_submit[track_id] = now_loop
                                except queue.Full:
                                    pass  # worker busy, drop -> keeps FPS high

                        # Stage 4: alarm decision (uses current config + resolved type)
                        if box_type == "stranger":
                            should_alarm = True
                            alert_reason = "intrusion"
                        elif box_type == "family":
                            family_safe_seen = True
                            if not system_config.get("ignore_alerts_for_family", True):
                                should_alarm = True
                                alert_reason = "intrusion"
                        # "pending" and "unknown" deliberately do NOT trigger alarm by themselves

                        if self._looks_like_fall((x1, y1, x2, y2), frame_local.shape):
                            label = "CANH BAO: Te nga"
                            box_type = "fall"
                            box_color = (0, 0, 255)

                        detected.append({
                            "box": (x1, y1, x2, y2),
                            "label": label,
                            "type": box_type,
                            "color": box_color
                        })

                    # Drop tracks that vanished so face_results doesn't leak memory
                    self._cleanup_stale_tracks(now_loop)
                except Exception as e:
                    print(f"[YOLO THREAD] Model inference error: {e}")
                    
            # Update global variables atomic values
            detected_boxes = detected
            is_person_detected = len(detected) > 0
            
            # --- 3. FILTER / STABILIZATION / ARMED TRIGGER LOGIC ---
            detect_time = time.time()
            if system_config["system_armed"]:
                fall_seen = any(isinstance(b, dict) and b.get("type") == "fall" for b in detected_boxes)

                if fall_seen:
                    last_fall_time = detect_time
                    if first_fall_time is None:
                        first_fall_time = detect_time
                    fall_detections += 1
                elif last_fall_time is not None and (detect_time - last_fall_time) >= 2.0:
                    fall_detections = 0
                    first_fall_time = None
                    last_fall_time = None

                fall_duration = (detect_time - first_fall_time) if first_fall_time else 0.0
                fall_ready = (
                    fall_detections >= 3
                    and fall_duration >= float(system_config.get("fall_stable_seconds", 1.5))
                )

                if fall_ready:
                    should_alarm = True
                    alert_reason = "fall"
                    
                if should_alarm:
                    last_seen_time = detect_time
                    if first_detect_time is None:
                        first_detect_time = detect_time
                    consecutive_detections += 1
                else:
                    consecutive_detections = 0
                    first_detect_time = None
                    last_seen_time = None
                    if family_safe_seen and system_config.get("ignore_alerts_for_family", True) and hw_controller.states["buzzer"]:
                        hw_controller.set_buzzer(False)
                        add_event("Face ID da nhan dien nguoi nha. He thong ve trang thai an toan, tat coi bao dong.", "success")
                        
                stable_duration = (detect_time - first_detect_time) if first_detect_time else 0.0
                
                # Check alert trigger threshold
                ready_to_alert = (
                    consecutive_detections >= 4 # stable frames count
                    and stable_duration >= system_config["stable_seconds"]
                )
                
                if ready_to_alert:
                    # 1. Trigger Buzzer immediately if configured
                    if system_config["buzzer_alerts_enabled"] and not hw_controller.states["buzzer"]:
                        hw_controller.set_buzzer(True)
                        add_event("⚠️ CẢNH BÁO ĐỘT NHẬP! Đã kích hoạt còi hú báo động.", "danger")
                        
                    # 2. Trigger Email alerts if cooldown has passed
                    cooldown_ok = (detect_time - last_email_time) >= system_config["cooldown_seconds"]
                    if system_config["email_alerts_enabled"] and cooldown_ok and not email_sending:
                        # Capture image snapshot to send
                        snapshot_path = os.path.join(BASE_DIR, "guardshield_snapshot.jpg")
                        cv2.imwrite(snapshot_path, frame_local)
                        
                        # Start sending email in a background daemon thread
                        threading.Thread(
                            target=send_email_alert,
                            args=(snapshot_path,),
                            daemon=True
                        ).start()
                        last_email_time = detect_time
                        
                    # 3. Trigger GSM Alerts (SMS & Calling) if cooldown has passed
                    gsm_cooldown_ok = (detect_time - last_gsm_time) >= system_config["cooldown_seconds"]
                    if gsm_cooldown_ok:
                        phone = system_config.get("alert_phone_number", "0901234567")
                        
                        # Send SMS if enabled
                        if system_config.get("sms_alerts_enabled", True):
                            sms_text = system_config.get(
                                "sms_message_template", 
                                "GuardShield AI Canh bao: Phat hien nguoi la xam nhap vao luc {time}!"
                            )
                            formatted_time = datetime.now().strftime("%H:%M:%S ngay %d/%m/%Y")
                            sms_text = sms_text.replace("{time}", formatted_time)
                            
                            threading.Thread(
                                target=gsm_controller.send_sms,
                                args=(phone, sms_text),
                                daemon=True
                            ).start()
                            
                        # Make warning phone call if enabled
                        if system_config.get("call_alerts_enabled", True):
                            threading.Thread(
                                target=gsm_controller.make_call,
                                args=(phone,),
                                daemon=True
                            ).start()
                            
                        last_gsm_time = detect_time
            else:
                # System disarmed: Turn off buzzer/siren if it was on
                if hw_controller.states["buzzer"]:
                    hw_controller.set_buzzer(False)
                consecutive_detections = 0
                first_detect_time = None
                
            # Calculation of FPS
            now = time.time()
            detection_fps = 1 / (now - prev_time)
            prev_time = now
            
            # Limit YOLO processing slightly to prevent CPU thermal throttling (target ~8fps)
            sleep_time = 0.12 - (time.time() - start_loop)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop(self):
        self.running = False
        # Wake the face worker so it can exit promptly
        try:
            self.face_queue.put_nowait(None)
        except Exception:
            pass
        if self.picam2:
            try:
                self.picam2.stop()
            except:
                pass
        if self.cam:
            try:
                self.cam.release()
            except:
                pass
        print("[CAMERA] Camera detector threads stopped.")

camera_detector = CameraDetector()

# ----------------- FLASK WEB SERVER INITS -----------------
app = Flask(__name__, static_folder="website", static_url_path="")

# Disable access logs to prevent console cluttering
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def get_cpu_temp():
    try:
        # Works on Raspberry Pi OS
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = float(f.read()) / 1000.0
            return round(temp, 1)
    except:
        return 42.5  # Standard mockup temp for PC simulation

@app.route('/')
def index():
    return send_from_directory(WEBSITE_DIR, "index.html")

# Static files fallback
@app.route('/style.css')
def style():
    return send_from_directory(WEBSITE_DIR, "style.css")

@app.route('/app.js')
def app_js():
    return send_from_directory(WEBSITE_DIR, "app.js")

@app.route('/logo.png')
def logo():
    return send_from_directory(WEBSITE_DIR, "logo.png")

# Real-time Video Stream Endpoint (MJPEG)
@app.route('/video_feed')
def video_feed():
    def generate():
        global encoded_jpeg_frame
        while True:
            if encoded_jpeg_frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + encoded_jpeg_frame + b'\r\n')
            time.sleep(0.033) # limit to 30 FPS stream
            
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# API Endpoints
@app.route('/api/status', methods=['GET'])
def api_status():
    status_data = {
        "system_armed": system_config["system_armed"],
        "email_alerts_enabled": system_config["email_alerts_enabled"],
        "buzzer_alerts_enabled": system_config["buzzer_alerts_enabled"],
        
        # New SMS and Call alerts status
        "sms_alerts_enabled": system_config.get("sms_alerts_enabled", True),
        "call_alerts_enabled": system_config.get("call_alerts_enabled", True),
        
        "device_1_state": hw_controller.states[1],
        "device_2_state": hw_controller.states[2],
        "device_3_state": hw_controller.states[3],
        "servo_state": hw_controller.states["servo"],
        "buzzer_state": hw_controller.states["buzzer"],
        "device_1_name": system_config["device_1_name"],
        "device_2_name": system_config["device_2_name"],
        "device_3_name": system_config["device_3_name"],
        "is_person_detected": is_person_detected,
        "detection_fps": round(detection_fps, 1),
        "cpu_temp": get_cpu_temp(),
        "camera_mode": camera_detector.mode,
        "email_sending": email_sending,
        "gpio_mode": hw_controller.mode,
        
        # Face recognition details
        "known_faces_count": len(face_id_manager.list_faces())
    }
    return jsonify(status_data)

@app.route('/api/control_device', methods=['POST'])
def api_control_device():
    data = request.json or {}
    device_id = data.get("device_id") # 1, 2, 3, 'servo' or 'buzzer'
    state = data.get("state") # True/False
    
    if state is None or device_id is None:
        return jsonify({"success": False, "error": "Invalid arguments"}), 400
        
    if device_id in [1, 2, 3]:
        hw_controller.set_device(device_id, state)
        name = system_config.get(f"device_{device_id}_name")
        add_event(f"Thiết bị '{name}' được thay đổi trạng thái sang: {'BẬT' if state else 'TẮT'} thủ công.", "info")
        return jsonify({"success": True})

    elif device_id == "servo":
        hw_controller.set_servo(state)
        add_event(f"Servo SG90 được {'mở 90 độ' if state else 'đóng về 0 độ'} thủ công.", "info")
        return jsonify({"success": True})
        
    elif device_id == "buzzer":
        hw_controller.set_buzzer(state)
        add_event(f"Còi còi báo động được {'BẬT' if state else 'TẮT'} thủ công.", "warning")
        return jsonify({"success": True})
        
    return jsonify({"success": False, "error": "Unknown device"}), 400

@app.route('/api/toggle_alert', methods=['POST'])
def api_toggle_alert():
    data = request.json or {}
    alert_type = data.get("type") # 'email', 'buzzer', 'sms', 'call' or 'system'
    state = data.get("state") # True/False
    
    if state is None or alert_type is None:
        return jsonify({"success": False, "error": "Invalid arguments"}), 400
        
    if alert_type == "email":
        system_config["email_alerts_enabled"] = bool(state)
        save_config(system_config)
        add_event(f"Thay đổi cấu hình: {'KÍCH HOẠT' if state else 'VÔ HIỆU HÓA'} cảnh báo qua Email.", "info")
        return jsonify({"success": True})
        
    elif alert_type == "buzzer":
        system_config["buzzer_alerts_enabled"] = bool(state)
        save_config(system_config)
        add_event(f"Thay đổi cấu hình: {'KÍCH HOẠT' if state else 'VÔ HIỆU HÓA'} còi Buzzer khi có báo động.", "info")
        return jsonify({"success": True})
        
    elif alert_type == "sms":
        system_config["sms_alerts_enabled"] = bool(state)
        save_config(system_config)
        add_event(f"Thay đổi cấu hình: {'KÍCH HOẠT' if state else 'VÔ HIỆU HÓA'} gửi tin nhắn SMS cảnh báo.", "info")
        return jsonify({"success": True})
        
    elif alert_type == "call":
        system_config["call_alerts_enabled"] = bool(state)
        save_config(system_config)
        add_event(f"Thay đổi cấu hình: {'KÍCH HOẠT' if state else 'VÔ HIỆU HÓA'} gọi điện thoại báo động.", "info")
        return jsonify({"success": True})
        
    elif alert_type == "system":
        system_config["system_armed"] = bool(state)
        save_config(system_config)
        add_event(f"🛡️ HỆ THỐNG AN NINH: {'KÍCH HOẠT GIÁM SÁT (ARMED)' if state else 'TẮT GIÁM SÁT (DISARMED)'}.", "success" if state else "warning")
        
        # Turn off alarms immediately on disarm
        if not state:
            hw_controller.set_buzzer(False)
            
        return jsonify({"success": True})
        
    return jsonify({"success": False, "error": "Unknown alert type"}), 400

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    global system_config
    if request.method == 'GET':
        return jsonify(system_config)
    else:
        new_config = request.json or {}
        # Update config fields safely
        for key in system_config.keys():
            if key in new_config:
                # Maintain data types
                if isinstance(system_config[key], bool):
                    system_config[key] = bool(new_config[key])
                elif isinstance(system_config[key], float):
                    system_config[key] = float(new_config[key])
                elif isinstance(system_config[key], int):
                    system_config[key] = int(new_config[key])
                else:
                    system_config[key] = str(new_config[key])
                    
        save_config(system_config)
        gsm_controller.init_serial() # Re-init SIM connection in case serial settings changed
        add_event("Cấu hình hệ thống an ninh đã được cập nhật thành công.", "success")
        return jsonify({"success": True})

# ----------------- FACE RECOGNITION API ENDPOINTS -----------------
@app.route('/api/known_faces', methods=['GET'])
def api_known_faces():
    faces = face_id_manager.list_faces()
    return jsonify({"success": True, "faces": faces})

@app.route('/api/upload_face', methods=['POST'])
def api_upload_face():
    if 'file' not in request.files or 'name' not in request.form:
        return jsonify({"success": False, "error": "Thiếu tệp ảnh hoặc tên thành viên!"}), 400
        
    file = request.files['file']
    name = request.form['name'].strip()
    
    if not name:
        return jsonify({"success": False, "error": "Tên thành viên không được để trống!"}), 400
        
    if file.filename == '':
        return jsonify({"success": False, "error": "Tên tệp không hợp lệ!"}), 400
        
    try:
        # Normalize name for filename: strip Vietnamese diacritics first, then sanitize
        ascii_name = remove_vietnamese_accents(name)
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', ascii_name).strip('_')
        if not safe_name:
            return jsonify({"success": False, "error": "Tên không hợp lệ sau khi chuẩn hoá!"}), 400
        filename = f"{safe_name}.jpg"
        filepath = os.path.join(KNOWN_FACES_DIR, filename)

        # Save the file
        file.save(filepath)

        # Reload faces
        face_id_manager.load_known_faces()

        add_event(f"Thành viên mới '{name}' đăng ký thành công.", "success")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi lưu trữ ảnh: {str(e)}"}), 500

@app.route('/api/capture_face', methods=['POST'])
def api_capture_face():
    """Register a new face by capturing the current camera frame.
    More accurate than uploaded photos because lighting/angle/lens match runtime."""
    data = request.json or {}
    name = (data.get("name") or "").strip()

    if not name:
        return jsonify({"success": False, "error": "Tên thành viên không được để trống!"}), 400

    if raw_frame is None:
        return jsonify({"success": False, "error": "Camera chưa sẵn sàng. Vui lòng đợi vài giây và thử lại."}), 400

    frame = raw_frame.copy()

    # Validate the frame has exactly one detectable face before saving
    if FACE_REC_AVAILABLE:
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame, model="hog", number_of_times_to_upsample=1)
        except Exception as e:
            return jsonify({"success": False, "error": f"Lỗi quét khuôn mặt: {str(e)}"}), 500

        if len(face_locations) == 0:
            return jsonify({"success": False, "error": "Không thấy khuôn mặt trong khung hình. Hãy đứng thẳng trước camera, đủ ánh sáng và thử lại."}), 400
        if len(face_locations) > 1:
            return jsonify({"success": False, "error": f"Phát hiện {len(face_locations)} khuôn mặt. Chỉ chụp 1 người mỗi lần."}), 400

    # Normalize name -> safe filename
    ascii_name = remove_vietnamese_accents(name)
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', ascii_name).strip('_')
    if not safe_name:
        return jsonify({"success": False, "error": "Tên không hợp lệ sau khi chuẩn hoá!"}), 400

    filename = f"{safe_name}.jpg"
    filepath = os.path.join(KNOWN_FACES_DIR, filename)

    try:
        # JPEG quality 92 to keep facial features sharp for encoding
        ok = cv2.imwrite(filepath, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            return jsonify({"success": False, "error": "Không ghi được ảnh ra đĩa."}), 500

        face_id_manager.load_known_faces()
        add_event(f"📸 Đã đăng ký '{name}' bằng camera Pi.", "success")
        return jsonify({"success": True, "filename": filename})
    except Exception as e:
        return jsonify({"success": False, "error": f"Lỗi lưu ảnh: {str(e)}"}), 500

@app.route('/api/delete_face', methods=['POST'])
def api_delete_face():
    data = request.json or {}
    name = data.get("name", "").strip()
    
    if not name:
        return jsonify({"success": False, "error": "Thiếu tên thành viên để xóa!"}), 400
        
    if face_id_manager.delete_face(name):
        add_event(f"Đã xóa thành viên '{name}' khỏi danh sách nhận diện.", "warning")
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Không tìm thấy thành viên cần xóa hoặc lỗi tệp."}), 400

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    user = data.get("username")
    passw = data.get("password")
    
    saved_user = system_config.get("web_username", "admin")
    saved_pass = system_config.get("web_password", "123456")
    
    if user == saved_user and passw == saved_pass:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Sai tên đăng nhập hoặc mật khẩu!"})

@app.route('/api/logs', methods=['GET'])
def api_logs():
    return jsonify(event_logs)

@app.route('/api/manual_trigger', methods=['POST'])
def api_manual_trigger():
    # Force a manual security intrusion simulation trigger (great for testing alerts!)
    add_event("🚨 KÍCH HOẠT BÁO ĐỘNG KHẨN CẤP THỦ CÔNG 🚨", "danger")
    if system_config["buzzer_alerts_enabled"]:
        hw_controller.set_buzzer(True)
        
    if system_config["email_alerts_enabled"] and not email_sending:
        # Send instant email using last captured frame
        snapshot_path = os.path.join(BASE_DIR, "guardshield_snapshot.jpg")
        if raw_frame is not None:
            cv2.imwrite(snapshot_path, raw_frame)
            
        threading.Thread(
            target=send_email_alert,
            args=(snapshot_path,),
            daemon=True
        ).start()
        
    return jsonify({"success": True})

# ----------------- MAIN LAUNCH SEQUENCE -----------------
if __name__ == "__main__":
    # Create a clean exit sequence
    try:
        # Flip running ON before launching threads so workers don't exit immediately
        camera_detector.running = True

        # Start camera acquisition loop in background
        capture_thread = threading.Thread(target=camera_detector.run_capture, daemon=True)
        capture_thread.start()

        # Start YOLO async inference loop in background (light, no face recog inside)
        yolo_thread = threading.Thread(target=camera_detector.run_inference, daemon=True)
        yolo_thread.start()

        # Start dedicated face recognition worker — heavy dlib calls run here, off the YOLO loop
        face_thread = threading.Thread(target=camera_detector._face_worker, daemon=True)
        face_thread.start()

        # Start Flask Server
        print("[SYSTEM] GuardShield AI Security server launching at http://0.0.0.0:5000")
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

    except KeyboardInterrupt:
        print("\n[SYSTEM] Server shutting down safely...")
    finally:
        camera_detector.stop()
        hw_controller.cleanup()
        print("[SYSTEM] All background systems terminated safely.")
