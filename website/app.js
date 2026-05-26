const app = {
  // ── SYSTEM STATE ──
  state: {
    isAuthenticated: false,
    systemArmed: false,
    emailAlertsEnabled: false,
    buzzerAlertsEnabled: false,
    smsAlertsEnabled: false,
    callAlertsEnabled: false,
    knownFaces: [],
    devices: {
      1: { name: "Thiết bị 1", state: false },
      2: { name: "Thiết bị 2", state: false },
      3: { name: "Thiết bị 3", state: false }
    },
    buzzerState: false,
    cpuTemp: 0.0,
    fps: 0.0,
    isPersonDetected: false,
    cameraMode: "simulation",
    eventLogs: [],
    pollingInterval: null,
    themeMode: 'dark',
    cpuHistory: []
  },

  // ── INITIALIZATION ──
  init() {
    // Load cached theme mode immediately on startup
    const savedTheme = localStorage.getItem('guardshield_theme') || 'dark';
    this.state.themeMode = savedTheme;
    if (savedTheme === 'light') {
      document.body.classList.add('light-mode');
    } else {
      document.body.classList.remove('light-mode');
    }

    // Toggle icons based on theme
    setTimeout(() => {
      const sun = document.getElementById('theme-icon-sun');
      const moon = document.getElementById('theme-icon-moon');
      if (sun && moon) {
        if (savedTheme === 'light') {
          sun.style.display = 'none';
          moon.style.display = 'block';
        } else {
          sun.style.display = 'block';
          moon.style.display = 'none';
        }
      }
    }, 100);

    this.checkAuth();
    if (this.state.isAuthenticated) {
      this.startApp();
    }
  },

  checkAuth() {
    const savedAuth = localStorage.getItem('guardshield_auth');
    const savedArmed = localStorage.getItem('guardshield_system_armed');
    if (savedArmed !== null) {
      this.state.systemArmed = (savedArmed === 'true');
    }
    
    if (savedAuth === 'true') {
      this.state.isAuthenticated = true;
      document.getElementById('login-view').classList.remove('active');
      document.getElementById('main-layout').classList.add('active');
    } else {
      this.state.isAuthenticated = false;
      document.getElementById('login-view').classList.add('active');
      document.getElementById('main-layout').classList.remove('active');
    }
  },

  async login() {
    const user = document.getElementById('username').value;
    const pass = document.getElementById('password').value;

    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, password: pass })
      });
      
      if (response.ok) {
        const result = await response.json();
        if (result.success) {
          this.state.isAuthenticated = true;
          localStorage.setItem('guardshield_auth', 'true');
          document.getElementById('login-view').classList.remove('active');
          document.getElementById('main-layout').classList.add('active');
          this.startApp();
          this.showToast('Hệ thống an ninh đã xác thực thành công!');
        } else {
          this.showToast(result.error || 'Sai thông tin đăng nhập!');
        }
      } else {
        throw new Error("HTTP error");
      }
    } catch (e) {
      // Fallback in case of offline/simulated PC when Flask is not running
      if (user === 'admin' && pass === '123456') {
        this.state.isAuthenticated = true;
        localStorage.setItem('guardshield_auth', 'true');
        document.getElementById('login-view').classList.remove('active');
        document.getElementById('main-layout').classList.add('active');
        this.startApp();
        this.showToast('Đã đăng nhập thành công (Chế độ mô phỏng offline)!');
      } else {
        this.showToast('Sai thông tin đăng nhập!');
      }
      console.error("Login fallback active:", e);
    }
  },

  logout() {
    this.state.isAuthenticated = false;
    localStorage.removeItem('guardshield_auth');
    if (this.state.pollingInterval) {
      clearInterval(this.state.pollingInterval);
    }
    document.getElementById('main-layout').classList.remove('active');
    document.getElementById('login-view').classList.add('active');
    document.getElementById('password').value = '';
    this.showToast('Đã đăng xuất khỏi hệ thống.');
  },

  startApp() {
    this.navigate('dashboard');
    this.loadConfig(); // Fetch configuration variables from Pi 5
    this.loadLogs();   // Fetch security logs
    
    // Start continuous status polling every 1 second (1000ms)
    this.state.pollingInterval = setInterval(() => this.pollStatus(), 1000);
    this.pollStatus(); // Immediate initial pull
  },

  // ── NAVIGATION MODULE ──
  navigate(pageId) {
    // Toggle active pages
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const targetPage = document.getElementById(pageId);
    if (targetPage) targetPage.classList.add('active');

    // Update bottom nav bar highlight
    document.querySelectorAll('.bnav-item').forEach(el => el.classList.remove('active'));
    const activeNav = document.querySelector(`.bnav-item[data-target="${pageId}"]`);
    if (activeNav) activeNav.classList.add('active');

    // Special triggers on navigation
    if (pageId === 'logs') {
      this.loadLogs();
    } else if (pageId === 'settings') {
      this.loadConfig();
    }

    window.scrollTo(0, 0);
  },

  // ── DATA POLLING & STATUS SYNC ──
  async pollStatus() {
    try {
      const response = await fetch('/api/status');
      if (!response.ok) throw new Error("Offline");
      
      const data = await response.json();
      
      // Update local states
      this.state.systemArmed = data.system_armed;
      localStorage.setItem('guardshield_system_armed', data.system_armed);
      this.state.emailAlertsEnabled = data.email_alerts_enabled;
      this.state.buzzerAlertsEnabled = data.buzzer_alerts_enabled;
      
      // Sync GSM Alerts
      this.state.smsAlertsEnabled = data.sms_alerts_enabled;
      this.state.callAlertsEnabled = data.call_alerts_enabled;
      
      this.state.devices[1] = { name: data.device_1_name, state: data.device_1_state };
      this.state.devices[2] = { name: data.device_2_name, state: data.device_2_state };
      this.state.devices[3] = { name: data.device_3_name, state: data.device_3_state };
      this.state.buzzerState = data.buzzer_state;
      this.state.cpuTemp = data.cpu_temp;
      
      // Update CPU Temp history slider for real-time charting
      if (!this.state.cpuHistory) this.state.cpuHistory = [];
      this.state.cpuHistory.push(data.cpu_temp);
      if (this.state.cpuHistory.length > 20) {
        this.state.cpuHistory.shift();
      }
      this.drawCpuChart();

      this.state.fps = data.detection_fps;
      this.state.isPersonDetected = data.is_person_detected;
      this.state.cameraMode = data.camera_mode;
      
      // Update UI displays
      this.updateStatusUI(true);
    } catch (error) {
      console.error("Polling error:", error);
      this.updateStatusUI(false); // Update UI to reflect offline state
    }
  },

  updateStatusUI(isOnline) {
    const statusDot = document.getElementById('system-status-dot');
    const statusText = document.getElementById('system-status-text');
    const cameraOffline = document.getElementById('camera-offline');
    const liveFps = document.getElementById('live-fps');
    const cameraModeBadge = document.getElementById('camera-mode-badge');
    const cameraHwInfo = document.getElementById('camera-hw-info');
    
    if (!isOnline) {
      // Offline HUD Display
      if (statusDot) {
        statusDot.className = 'status-dot';
        statusDot.style.background = 'var(--red)';
        statusDot.style.boxShadow = '0 0 10px var(--red)';
      }
      if (statusText) statusText.innerText = "Pi 5 Mất Kết Nối";
      if (cameraOffline) cameraOffline.classList.add('active');
      if (liveFps) liveFps.innerText = "FPS: 0.0";
      return;
    }
    
    // Online Display
    if (cameraOffline) cameraOffline.classList.remove('active');
    if (liveFps) liveFps.innerText = `AI FPS: ${this.state.fps.toFixed(1)}`;
    if (cameraModeBadge) {
      if (this.state.isPersonDetected && this.state.systemArmed) {
        cameraModeBadge.innerText = "🚨 CẢNH BÁO XÂM NHẬP 🚨";
        cameraModeBadge.parentElement.style.background = 'rgba(255, 71, 87, 0.25)';
        cameraModeBadge.parentElement.style.borderColor = 'rgba(255, 71, 87, 0.6)';
        cameraModeBadge.style.color = '#ff4757';
      } else {
        cameraModeBadge.innerText = this.state.cameraMode === 'simulation' ? "SIMULATOR ACTIVE" : "LIVE AI ACTIVE";
        cameraModeBadge.parentElement.style.background = '';
        cameraModeBadge.parentElement.style.borderColor = '';
        cameraModeBadge.style.color = '';
      }
    }
    
    if (cameraHwInfo) {
      cameraHwInfo.innerText = `Hardware: ${this.state.cameraMode === 'picamera2' ? 'CSI Camera' : this.state.cameraMode === 'webcam' ? 'USB Webcam' : 'Simulated Node'}`;
    }

    // Top Brand System Status Badge
    if (statusDot) {
      statusDot.className = 'status-dot';
      if (this.state.systemArmed) {
        statusDot.style.background = 'var(--green)';
        statusDot.style.boxShadow = '0 0 10px var(--green)';
        if (statusText) statusText.innerText = "Hệ thống: ARMED";
      } else {
        statusDot.style.background = 'var(--text-3)';
        statusDot.style.boxShadow = 'none';
        if (statusText) statusText.innerText = "Hệ thống: DISARMED";
      }
    }

    // Central Security Banner
    const homeArmStatus = document.getElementById('home-arm-status');
    if (homeArmStatus) {
      if (!this.state.systemArmed) {
        homeArmStatus.innerText = "VÔ HIỆU HÓA";
        homeArmStatus.style.color = "var(--text-3)";
      } else if (this.state.isPersonDetected) {
        homeArmStatus.innerText = "🚨 ĐỘT NHẬP 🚨";
        homeArmStatus.style.color = "var(--red)";
      } else {
        homeArmStatus.innerText = "AN TOÀN";
        homeArmStatus.style.color = "var(--green)";
      }
    }

    // Update Diagnostics
    const tempEl = document.getElementById('home-cpu-temp');
    if (tempEl) tempEl.innerText = `${this.state.cpuTemp.toFixed(1)}°C`;

    // Sync Buzzer Status Badge on Homepage
    const homeBuzzer = document.getElementById('home-buzzer-status');
    if (homeBuzzer) {
      if (this.state.buzzerState) {
        homeBuzzer.innerText = "🚨 ĐANG HÚ! 🚨";
        homeBuzzer.style.color = "var(--red)";
      } else {
        homeBuzzer.innerText = "YÊN LẶNG";
        homeBuzzer.style.color = "var(--text-3)";
      }
    }

    // Sync armed panic controls buttons
    const btnMasterArm = document.getElementById('btn-master-arm');
    if (btnMasterArm) {
      if (this.state.systemArmed) {
        btnMasterArm.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:24px;height:24px;margin-bottom:6px;">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
          HỦY GIÁM SÁT SYSTEM
        `;
        btnMasterArm.className = "btn-emergency btn-arm-toggle active";
      } else {
        btnMasterArm.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:24px;height:24px;margin-bottom:6px;">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
          KÍCH HOẠT HỆ THỐNG
        `;
        btnMasterArm.className = "btn-emergency btn-arm-toggle";
      }
    }

    // Sync Alert Mode configuration Toggles
    const toggleEmail = document.getElementById('toggle-email-alerts');
    if (toggleEmail) toggleEmail.checked = this.state.emailAlertsEnabled;

    const toggleBuzzer = document.getElementById('toggle-buzzer-alerts');
    if (toggleBuzzer) toggleBuzzer.checked = this.state.buzzerAlertsEnabled;

    const toggleSms = document.getElementById('toggle-sms-alerts');
    if (toggleSms) toggleSms.checked = this.state.smsAlertsEnabled;

    const toggleCall = document.getElementById('toggle-call-alerts');
    if (toggleCall) toggleCall.checked = this.state.callAlertsEnabled;

    // Sync 3 external smart devices switches & card classes
    for (let id = 1; id <= 3; id++) {
      const dev = this.state.devices[id];
      const switchEl = document.getElementById(`switch-dev-${id}`);
      const labelEl = document.getElementById(`label-dev-${id}`);
      const statusEl = document.getElementById(`status-dev-${id}`);
      const cardEl = document.getElementById(`device-card-${id}`);
      
      if (switchEl) switchEl.checked = dev.state;
      if (labelEl) labelEl.innerText = dev.name;
      if (statusEl) {
        statusEl.innerText = dev.state ? "ĐANG BẬT" : "ĐANG TẮT";
        statusEl.className = dev.state ? "dev-status-text active" : "dev-status-text";
      }
      if (cardEl) {
        if (dev.state) cardEl.classList.add('active');
        else cardEl.classList.remove('active');
      }

      // Synchronize Homepage Quick Device Status Cards
      const homeCardEl = document.getElementById(`home-dev-card-${id}`);
      const homeLabelEl = document.getElementById(`home-label-dev-${id}`);
      const homeStatusEl = document.getElementById(`home-status-dev-${id}`);
      const homeIconEl = document.getElementById(`home-dev-icon-${id}`);

      if (homeLabelEl) homeLabelEl.innerText = dev.name;
      if (homeStatusEl) {
        homeStatusEl.innerText = dev.state ? "ĐANG BẬT" : "ĐANG TẮT";
        homeStatusEl.style.color = dev.state ? "var(--green)" : "var(--text-3)";
      }
      if (homeCardEl) {
        if (dev.state) {
          homeCardEl.style.borderColor = "var(--green)";
          homeCardEl.style.boxShadow = "0 0 10px rgba(0, 230, 118, 0.15)";
          homeCardEl.style.background = "rgba(0, 230, 118, 0.04)";
        } else {
          homeCardEl.style.borderColor = "var(--border)";
          homeCardEl.style.boxShadow = "none";
          homeCardEl.style.background = "var(--bg-card)";
        }
      }
      if (homeIconEl) {
        if (dev.state) {
          homeIconEl.style.background = "var(--green-dim)";
          homeIconEl.style.borderColor = "rgba(0, 230, 118, 0.2)";
          const svg = homeIconEl.querySelector('svg');
          if (svg) svg.style.stroke = "var(--green)";
        } else {
          homeIconEl.style.background = "rgba(255,255,255,0.03)";
          homeIconEl.style.borderColor = "var(--border)";
          const svg = homeIconEl.querySelector('svg');
          if (svg) svg.style.stroke = "var(--text-3)";
        }
      }
    }

    // Sync Manual Siren buzzer switch
    const switchBuz = document.getElementById('switch-buzzer');
    if (switchBuz) switchBuz.checked = this.state.buzzerState;
  },

  // Toggle device from Homepage Quick Cards
  toggleHomeDevice(deviceId) {
    const dev = this.state.devices[deviceId];
    if (dev) {
      this.controlDevice(deviceId, !dev.state);
    }
  },

  // ── DEVICE ACTIONS & TRIGGERS ──
  async controlDevice(deviceId, state) {
    try {
      const response = await fetch('/api/control_device', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: deviceId, state: state })
      });
      
      if (response.ok) {
        if (deviceId === 'buzzer') {
          this.showToast(state ? "Đã Kích Hoạt Còi Hú Cảnh Báo!" : "Đã Tắt Còi Báo Động");
        } else {
          const devName = this.state.devices[deviceId]?.name || `Thiết bị ${deviceId}`;
          this.showToast(`${state ? 'Đã bật' : 'Đã tắt'} ${devName}`);
        }
        this.pollStatus(); // Sync state immediately
      } else {
        throw new Error("Failed to trigger device");
      }
    } catch (e) {
      this.showToast("Lỗi gửi lệnh điều khiển GPIO!");
      console.error(e);
    }
  },

  async toggleAlertMode(alertType, state) {
    try {
      const response = await fetch('/api/toggle_alert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: alertType, state: state })
      });
      
      if (response.ok) {
        const typeVietnamese = alertType === 'email' ? 'Cảnh báo Email' : 'Cảnh báo Còi Hú';
        this.showToast(`${state ? 'Đã kích hoạt' : 'Đã vô hiệu hóa'} ${typeVietnamese}`);
        this.pollStatus();
      } else {
        throw new Error("Failed to toggle alert configuration");
      }
    } catch (e) {
      this.showToast("Không thể thay đổi cấu hình báo động!");
      console.error(e);
    }
  },

  async toggleMasterArm() {
    const nextState = !this.state.systemArmed;
    this.state.systemArmed = nextState;
    localStorage.setItem('guardshield_system_armed', nextState);
    this.updateStatusUI(true); // Update immediately to feel super responsive

    try {
      const response = await fetch('/api/toggle_alert', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'system', state: nextState })
      });
      
      if (response.ok) {
        this.showToast(nextState ? "🛡️ HỆ THỐNG AN NINH: ĐÃ KÍCH HOẠT (ARMED)!" : "⚠️ HỆ THỐNG AN NINH: ĐÃ TẮT (DISARMED)");
        this.pollStatus();
      } else {
        throw new Error("Arming error");
      }
    } catch (e) {
      this.showToast("Không thể kết nối đến máy chủ an ninh!");
      console.error(e);
    }
  },

  async triggerPanic() {
    try {
      const response = await fetch('/api/manual_trigger', { method: 'POST' });
      if (response.ok) {
        this.showToast("🚨 BÁO ĐỘNG KHẨN CẤP ĐÃ ĐƯỢC PHÁT KHỞI THÀNH CÔNG!");
        this.pollStatus();
      } else {
        throw new Error("Panic fail");
      }
    } catch (e) {
      this.showToast("Lỗi kích hoạt khẩn cấp!");
      console.error(e);
    }
  },

  // ── THEME SWITCHER (LIGHT / DARK THEME) ──
  toggleTheme() {
    const nextTheme = this.state.themeMode === 'dark' ? 'light' : 'dark';
    this.state.themeMode = nextTheme;
    localStorage.setItem('guardshield_theme', nextTheme);
    
    if (nextTheme === 'light') {
      document.body.classList.add('light-mode');
      const sun = document.getElementById('theme-icon-sun');
      const moon = document.getElementById('theme-icon-moon');
      if (sun) sun.style.display = 'none';
      if (moon) moon.style.display = 'block';
    } else {
      document.body.classList.remove('light-mode');
      const sun = document.getElementById('theme-icon-sun');
      const moon = document.getElementById('theme-icon-moon');
      if (sun) sun.style.display = 'block';
      if (moon) moon.style.display = 'none';
    }
    
    this.showToast(`Đã chuyển sang giao diện ${nextTheme === 'light' ? 'Sáng' : 'Tối'}!`);
    this.drawCpuChart(); // Redraw chart immediately to update grid/line styles
  },

  // ── CPU TEMPERATURE SMOOTH CANVAS CHART ──
  drawCpuChart() {
    const canvas = document.getElementById('cpu-temp-chart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    // Set canvas internal resolution for retina sharp drawings
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = 80 * dpr; // height is 80px
    ctx.scale(dpr, dpr);
    
    const width = rect.width;
    const height = 80;
    
    // Clear canvas frame
    ctx.clearRect(0, 0, width, height);
    
    const data = this.state.cpuHistory || [];
    if (data.length < 2) {
      // Draw grid placeholder line
      ctx.strokeStyle = 'rgba(0, 242, 254, 0.15)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
      return;
    }
    
    // Find min & max bounds
    let min = Math.min(...data) - 1.0;
    let max = Math.max(...data) + 1.0;
    if (max - min < 4) {
      min = min - 1.5;
      max = max + 1.5;
    }
    
    // Update dynamic range description label
    const maxTempEl = document.getElementById('chart-max-temp');
    if (maxTempEl) maxTempEl.innerText = `Range: ${min.toFixed(1)}°C - ${max.toFixed(1)}°C`;
    
    const points = [];
    const stepX = width / (data.length - 1);
    
    for (let i = 0; i < data.length; i++) {
      const x = i * stepX;
      const y = height - ((data[i] - min) / (max - min)) * (height - 16) - 8;
      points.push({ x, y });
    }
    
    // 1. Draw smooth gradient fills under the line
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    const accentColor = document.body.classList.contains('light-mode') ? 'rgba(0, 242, 254, 0.25)' : 'rgba(0, 242, 254, 0.12)';
    grad.addColorStop(0, accentColor);
    grad.addColorStop(1, 'rgba(0, 242, 254, 0)');
    
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.moveTo(0, height);
    points.forEach(p => ctx.lineTo(p.x, p.y));
    ctx.lineTo(width, height);
    ctx.closePath();
    ctx.fill();
    
    // 2. Draw micro cybernetic background grid lines
    ctx.strokeStyle = document.body.classList.contains('light-mode') ? 'rgba(0, 0, 0, 0.04)' : 'rgba(255, 255, 255, 0.03)';
    ctx.lineWidth = 1;
    for (let val = Math.ceil(min); val <= Math.floor(max); val += 2) {
      const y = height - ((val - min) / (max - min)) * (height - 16) - 8;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
    
    // 3. Draw neon glow curve line
    ctx.strokeStyle = 'var(--accent)';
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    
    if (!document.body.classList.contains('light-mode')) {
      ctx.shadowBlur = 10;
      ctx.shadowColor = 'rgba(0, 242, 254, 0.4)';
    } else {
      ctx.shadowBlur = 0;
    }
    
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i++) {
      ctx.lineTo(points[i].x, points[i].y);
    }
    ctx.stroke();
    
    // Clear shadows
    ctx.shadowBlur = 0;
    
    // 4. Highlight the points
    ctx.fillStyle = 'var(--accent)';
    points.forEach((p, idx) => {
      if (idx === points.length - 1) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2);
        ctx.fill();
      } else if (idx % 2 === 0) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
        ctx.fill();
      }
    });
  },

  // ── CONFIGURATION PANEL ──
  async loadConfig() {
    try {
      const response = await fetch('/api/config');
      if (response.ok) {
        const config = await response.json();
        
        // Load settings to HTML input fields
        const fields = [
          'sender_email', 'app_password', 'receiver_email', 
          'confidence', 'cooldown_seconds', 'stable_seconds',
          'device_1_name', 'device_2_name', 'device_3_name',
          'web_username', 'web_password',
          // GSM and Face ID configurations
          'alert_phone_number', 'sms_message_template', 'sim_port', 'sim_baudrate',
          'face_recognition_enabled', 'face_distance_tolerance', 'ignore_alerts_for_family', 'alarm_on_undetected_faces',
          'sms_alerts_enabled', 'call_alerts_enabled'
        ];
        
        fields.forEach(field => {
          const input = document.getElementById(field);
          if (input) {
            if (input.type === 'checkbox') {
              input.checked = !!config[field];
            } else {
              input.value = config[field] !== undefined ? config[field] : '';
            }
          }
        });

        // Sync slider display values
        const toleranceSlider = document.getElementById('face_distance_tolerance');
        const faceToleranceVal = document.getElementById('face-tolerance-val');
        if (toleranceSlider && faceToleranceVal) {
          faceToleranceVal.innerText = parseFloat(toleranceSlider.value).toFixed(2);
        }
      }
    } catch (e) {
      console.error("Config fetch error:", e);
    }
  },

  async saveSettings() {
    const newConfig = {};
    const fields = [
      'sender_email', 'app_password', 'receiver_email', 
      'confidence', 'cooldown_seconds', 'stable_seconds',
      'device_1_name', 'device_2_name', 'device_3_name',
      'web_username', 'web_password',
      // GSM and Face ID configurations
      'alert_phone_number', 'sms_message_template', 'sim_port', 'sim_baudrate',
      'face_recognition_enabled', 'face_distance_tolerance', 'ignore_alerts_for_family', 'alarm_on_undetected_faces',
      'sms_alerts_enabled', 'call_alerts_enabled'
    ];
    
    // Collect settings data
    fields.forEach(field => {
      const input = document.getElementById(field);
      if (input) {
        if (input.type === 'checkbox') {
          newConfig[field] = input.checked;
        } else if (input.type === 'number' || input.type === 'range') {
          newConfig[field] = parseFloat(input.value);
        } else {
          newConfig[field] = input.value;
        }
      }
    });

    try {
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig)
      });
      
      if (response.ok) {
        this.showToast("⚙️ Đã lưu cấu hình an ninh lên Pi 5 thành công!");
        this.pollStatus(); // Pull status immediately to update labels
      } else {
        throw new Error("Config save failed");
      }
    } catch (e) {
      this.showToast("Lỗi lưu trữ cấu hình trên Pi 5!");
      console.error(e);
    }
  },

  switchSettingsTab(tabName) {
    // 1. Update Sidebar Active Button State
    const buttons = document.querySelectorAll('.settings-tab-btn');
    buttons.forEach(btn => {
      if (btn.getAttribute('data-settings-tab') === tabName) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
    
    // 2. Toggle Panel Visibility
    const panels = document.querySelectorAll('.settings-panel');
    panels.forEach(panel => {
      if (panel.id === `settings-panel-${tabName}`) {
        panel.classList.add('active');
      } else {
        panel.classList.remove('active');
      }
    });

    // 3. Keep mobile selector dropdown states in sync
    const mobileTriggerLabel = document.getElementById('active-settings-tab-name');
    if (mobileTriggerLabel) {
      const labels = {
        'mail': '📧 Cấu hình Mail',
        'gsm': '📱 Module GSM SIM',
        'faceid': '👤 Nhận diện & Thành viên',
        'ai': '🧠 Thông số AI',
        'devices': '🔧 Tên Thiết bị',
        'account': '🔒 Tài khoản Web'
      };
      if (labels[tabName]) {
        mobileTriggerLabel.innerHTML = labels[tabName];
      }
    }

    // Trigger face list loading if faceid tab is active
    if (tabName === 'faceid') {
      this.loadKnownFaces();
    }

    const mobileItems = document.querySelectorAll('.settings-mobile-menu-item');
    mobileItems.forEach(item => {
      if (item.getAttribute('onclick').includes(`'${tabName}'`)) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });
  },

  toggleSettingsMenu() {
    const list = document.getElementById('settings-mobile-menu-list');
    const arrow = document.getElementById('settings-menu-arrow');
    if (list && arrow) {
      const isOpen = list.classList.contains('open');
      if (isOpen) {
        list.classList.remove('open');
        arrow.style.transform = 'rotate(0deg)';
      } else {
        list.classList.add('open');
        arrow.style.transform = 'rotate(180deg)';
      }
    }
  },

  selectMobileSettingsTab(tabName, tabLabel) {
    // Switch active settings tab
    this.switchSettingsTab(tabName);
    
    // Close the dropdown menu list
    const list = document.getElementById('settings-mobile-menu-list');
    const arrow = document.getElementById('settings-menu-arrow');
    if (list && arrow) {
      list.classList.remove('open');
      arrow.style.transform = 'rotate(0deg)';
    }
  },

  // ── HISTORICAL LOG EVENTS ──
  async loadLogs() {
    const listWrapper = document.getElementById('event-logs-list');
    try {
      const response = await fetch('/api/logs');
      if (response.ok) {
        const logs = await response.json();
        this.state.eventLogs = logs;
        
        if (logs.length === 0) {
          listWrapper.innerHTML = `<div class="log-empty-state">Hộp nhật ký trống. Chưa ghi nhận sự kiện bảo mật nào.</div>`;
          return;
        }

        let html = '';
        logs.forEach(log => {
          let badgeClass = 'log-badge-info';
          if (log.category === 'success') badgeClass = 'log-badge-success';
          else if (log.category === 'danger') badgeClass = 'log-badge-danger';
          else if (log.category === 'warning') badgeClass = 'log-badge-warning';
          
          html += `
            <div class="log-item fade-up">
              <div class="log-item-meta">
                <span class="log-time">${log.timestamp}</span>
                <span class="log-badge ${badgeClass}">${log.category.toUpperCase()}</span>
              </div>
              <div class="log-desc">${log.description}</div>
            </div>
          `;
        });
        listWrapper.innerHTML = html;
      }
    } catch (e) {
      listWrapper.innerHTML = `<div class="log-empty-state" style="color:var(--red);">Không thể tải nhật ký an ninh</div>`;
      console.error(e);
    }
  },

  // ── UTILITIES (TOAST DIALOGS) ──
  showToast(msg) {
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2800);
  },

  // ── FACE RECOGNITION DATABASE ACTIONS ──
  async loadKnownFaces() {
    const wrapper = document.getElementById('registered-faces-list');
    if (!wrapper) return;
    try {
      const response = await fetch('/api/known_faces');
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          this.state.knownFaces = data.faces;
          if (data.faces.length === 0) {
            wrapper.innerHTML = `<div style="font-size: 11px; color: var(--text-3); text-align: center; padding: 10px;">Chưa đăng ký gương mặt người nhà nào.</div>`;
            return;
          }
          
          let html = '';
          data.faces.forEach(name => {
            html += `
              <div class="face-item" style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px;">
                <span style="font-size: 13px; font-weight: 600; color: var(--text-2);">${name}</span>
                <button type="button" onclick="app.deleteFace('${name}')" style="background: rgba(255, 51, 102, 0.1); border: 1px solid rgba(255, 51, 102, 0.2); color: var(--red); font-size: 11px; font-weight: 700; padding: 4px 8px; border-radius: 4px; cursor: pointer; transition: all 0.2s; border: none;">Xóa</button>
              </div>
            `;
          });
          wrapper.innerHTML = html;
        }
      }
    } catch (e) {
      console.error("Error loading faces:", e);
      wrapper.innerHTML = `<div style="font-size: 11px; color: var(--red); text-align: center; padding: 10px;">Lỗi tải danh sách gương mặt.</div>`;
    }
  },

  async uploadFace() {
    const nameInput = document.getElementById('new-face-name');
    const fileInput = document.getElementById('new-face-file');
    if (!nameInput || !fileInput) return;
    
    const name = nameInput.value.trim();
    const file = fileInput.files[0];
    
    if (!name) {
      this.showToast("Vui lòng điền họ và tên thành viên!");
      return;
    }
    if (!file) {
      this.showToast("Vui lòng chọn tệp hình ảnh khuôn mặt!");
      return;
    }
    
    const formData = new FormData();
    formData.append('name', name);
    formData.append('file', file);
    
    this.showToast("Đang tải gương mặt lên...");
    try {
      const response = await fetch('/api/upload_face', {
        method: 'POST',
        body: formData
      });
      
      if (response.ok) {
        const result = await response.json();
        if (result.success) {
          this.showToast(`Đã thêm thành viên ${name} thành công!`);
          nameInput.value = '';
          fileInput.value = '';
          this.loadKnownFaces();
          this.pollStatus();
        } else {
          this.showToast(result.error || "Đăng ký gương mặt thất bại!");
        }
      } else {
        throw new Error("HTTP error");
      }
    } catch (e) {
      this.showToast("Lỗi gửi ảnh lên máy chủ!");
      console.error(e);
    }
  },

  async captureFace() {
    const nameInput = document.getElementById('new-face-name');
    if (!nameInput) return;

    const name = nameInput.value.trim();
    if (!name) {
      this.showToast("Vui lòng điền họ và tên thành viên trước khi chụp!");
      nameInput.focus();
      return;
    }

    if (!confirm(`Chuẩn bị chụp ảnh "${name}" từ camera Pi. Đứng thẳng trước camera, đủ ánh sáng, chỉ 1 người trong khung hình. Tiếp tục?`)) {
      return;
    }

    this.showToast("📸 Đang chụp từ camera Pi...");
    try {
      const response = await fetch('/api/capture_face', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
      });

      const result = await response.json();
      if (response.ok && result.success) {
        this.showToast(`✅ Đã đăng ký ${name} bằng camera Pi!`);
        nameInput.value = '';
        const fileInput = document.getElementById('new-face-file');
        if (fileInput) fileInput.value = '';
        this.loadKnownFaces();
        this.pollStatus();
      } else {
        this.showToast(result.error || "Chụp khuôn mặt thất bại!");
      }
    } catch (e) {
      this.showToast("Lỗi kết nối tới máy chủ khi chụp!");
      console.error(e);
    }
  },

  async deleteFace(name) {
    if (!confirm(`Bạn có chắc chắn muốn xóa thành viên ${name} khỏi danh sách nhận diện?`)) {
      return;
    }
    
    try {
      const response = await fetch('/api/delete_face', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
      });
      
      if (response.ok) {
        const result = await response.json();
        if (result.success) {
          this.showToast(`Đã xóa thành công thành viên ${name}.`);
          this.loadKnownFaces();
          this.pollStatus();
        } else {
          this.showToast(result.error || "Xóa thành viên thất bại!");
        }
      } else {
        throw new Error("HTTP error");
      }
    } catch (e) {
      this.showToast("Lỗi gửi yêu cầu xóa lên máy chủ!");
      console.error(e);
    }
  }
};

// Start application on page load
window.addEventListener('DOMContentLoaded', () => app.init());
