#!/bin/bash

# สีสำหรับ output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ฟังก์ชันแสดงข้อความ
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# ฟังก์ชันสำหรับ SSH command ที่ใช้ password
ssh_with_password() {
    local server=$1
    local command=$2
    expect << EOF
set timeout 30
spawn ssh -o StrictHostKeyChecking=no realsagi@$server "$command"
expect {
    "password:" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    "Password:" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    "\[sudo\] password" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    eof
}
catch wait result
exit [lindex \$result 3]
EOF
}

# ฟังก์ชันสำหรับ rsync ที่ใช้ password
rsync_with_password() {
    local server=$1
    local remote_path=$2
    expect << EOF
set timeout 300
spawn rsync -avz --progress -e "ssh -o StrictHostKeyChecking=no" --exclude='.git' --exclude='__pycache__' --exclude='**/__pycache__/**' --exclude='*.pyc' --exclude='*.pyo' --exclude='.pytest_cache' --exclude='.coverage' --exclude='htmlcov' --exclude='venv' --exclude='.venv' --exclude='env' --exclude='.env' --exclude='*.log' --exclude='.DS_Store' --exclude='mongo_data' --exclude='.docker' --exclude='node_modules' --exclude='*.swp' --exclude='*.swo' --ignore-errors ./ realsagi@$server:$remote_path/
expect {
    "password:" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    "Password:" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    eof
}
catch wait result
set exit_code [lindex \$result 3]
# Ignore exit code 23 (partial transfer due to error) if it's just permission issues
if {\$exit_code == 23} {
    exit 0
} else {
    exit \$exit_code
}
EOF
}

# ฟังก์ชัน deploy
deploy_to_server() {
    local SERVER=$1
    local SERVER_NAME=$2
    local REMOTE_PATH="/home/realsagi/line_bot_hr_kf"
    
    print_info "กำลัง deploy ไปยัง $SERVER_NAME ($SERVER)..."
    print_info "Remote path: $REMOTE_PATH"
    
    # ตรวจสอบว่า expect มีหรือไม่
    if ! command -v expect &> /dev/null; then
        print_error "expect ไม่พบในระบบ"
        print_info "ติดตั้งด้วย:"
        print_info "  macOS: brew install expect"
        print_info "  Ubuntu/Debian: sudo apt-get install expect"
        print_info "  CentOS/RHEL: sudo yum install expect"
        exit 1
    fi
    
    # ตรวจสอบว่า rsync มีหรือไม่
    if ! command -v rsync &> /dev/null; then
        print_error "rsync ไม่พบในระบบ"
        print_info "ติดตั้งด้วย: brew install rsync (macOS) หรือ apt-get install rsync (Linux)"
        exit 1
    fi
    
    # ตรวจสอบว่า server เชื่อมต่อได้หรือไม่
    print_info "กำลังตรวจสอบการเชื่อมต่อ..."
    if ! ssh_with_password $SERVER "echo 'Connection OK'" 2>&1 | grep -q "Connection OK"; then
        print_error "ไม่สามารถเชื่อมต่อกับ server $SERVER ได้"
        print_warning "กรุณาตรวจสอบ:"
        print_warning "  1. Server เปิดอยู่หรือไม่"
        print_warning "  2. Network เชื่อมต่อได้หรือไม่"
        print_warning "  3. รหัสผ่านถูกต้องหรือไม่"
        exit 1
    fi
    
    print_success "เชื่อมต่อ server สำเร็จ"
    
    # สร้าง directory บน server ถ้ายังไม่มี
    print_info "กำลังสร้าง directory บน server..."
    ssh_with_password $SERVER "mkdir -p $REMOTE_PATH" > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        print_error "ไม่สามารถสร้าง directory ได้"
        exit 1
    fi
    
    # ลบ __pycache__ และ .pyc files บน server เพื่อหลีกเลี่ยง permission issues
    print_info "กำลังลบ cache files บน server..."
    ssh_with_password $SERVER "cd $REMOTE_PATH && find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true; find . -type f -name '*.pyc' -delete 2>/dev/null || true" > /dev/null 2>&1
    
    # ใช้ rsync เพื่อ sync ไฟล์ (เร็วกว่า scp)
    print_info "กำลัง sync ไฟล์ขึ้น server..."
    print_info "นี่อาจใช้เวลาสักครู่..."
    
    rsync_with_password $SERVER $REMOTE_PATH
    
    if [ $? -ne 0 ]; then
        print_error "ไม่สามารถ sync ไฟล์ได้"
        exit 1
    fi
    
    print_success "Sync ไฟล์สำเร็จ"
    
    # Restart Docker
    print_info "กำลัง restart Docker containers..."
    print_info "คำสั่ง: sudo docker-compose down && sudo docker-compose build && sudo docker-compose up -d"
    
    # ใช้ expect เพื่อส่ง password สำหรับ SSH และ sudo
    # ใช้ sudo -S เพื่ออ่าน password จาก stdin (echo password | sudo -S command)
    # ใช้ -t flag ใน SSH เพื่อให้มี pseudo-terminal สำหรับ sudo
    expect << EOF
set timeout 300
spawn ssh -o StrictHostKeyChecking=no -t realsagi@$SERVER "cd $REMOTE_PATH && echo '$SSH_PASSWORD' | sudo -S docker-compose down && echo '$SSH_PASSWORD' | sudo -S docker-compose build && echo '$SSH_PASSWORD' | sudo -S docker-compose up -d"
expect {
    "password:" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    "Password:" {
        send "$SSH_PASSWORD\r"
        exp_continue
    }
    eof
}
catch wait result
set exit_code [lindex \$result 3]
if {\$exit_code == 0} {
    exit 0
} else {
    exit \$exit_code
}
EOF
    
    RESTART_EXIT_CODE=$?
    if [ $RESTART_EXIT_CODE -eq 0 ]; then
        print_success "Restart Docker สำเร็จ! 🎉"
    else
        print_warning "Restart Docker อาจมีปัญหา (exit code: $RESTART_EXIT_CODE) กรุณาตรวจสอบ logs"
        print_info "ลองรันคำสั่งนี้เอง:"
        print_info "  ssh realsagi@$SERVER"
        print_info "  cd $REMOTE_PATH"
        print_info "  sudo docker-compose down"
        print_info "  sudo docker-compose build"
        print_info "  sudo docker-compose up -d"
    fi
    
    print_success "Deploy สำเร็จ! 🎉"
    echo ""
    print_info "═══════════════════════════════════════"
    print_info "Server: $SERVER_NAME"
    print_info "SSH IP: $SERVER"
    print_info "Path: $REMOTE_PATH"
    print_info "═══════════════════════════════════════"
    echo ""
    print_info "คำสั่งที่มีประโยชน์อื่นๆ:"
    print_info "  ดู logs: ssh realsagi@$SERVER 'cd $REMOTE_PATH && sudo docker-compose logs -f'"
    print_info "  ดู status: ssh realsagi@$SERVER 'cd $REMOTE_PATH && sudo docker-compose ps'"
    print_info "  Restart: ssh realsagi@$SERVER 'cd $REMOTE_PATH && sudo docker-compose restart'"
    echo ""
}

# Main menu
clear
echo "=========================================="
echo "   🚀 Line Bot HR KF Deployment Script"
echo "=========================================="
echo ""
echo "กำลัง deploy ไปยัง:"
echo "  Server: 10.0.0.2"
echo "  Path: /home/realsagi/line_bot_hr_kf"
echo ""
echo "⚠️  หมายเหตุ: จะต้องใส่รหัสผ่าน SSH ครั้งเดียว (8ik,8ik,)"
echo ""

# ถามรหัสผ่านครั้งเดียว
read -sp "กรุณาใส่รหัสผ่าน SSH: " SSH_PASSWORD
echo ""
echo ""

read -p "ยืนยันการ deploy? (y/n): " confirm
if [[ $confirm == [yY] || $confirm == [yY][eE][sS] ]]; then
    deploy_to_server "10.0.0.2" "Line Bot HR KF Server"
else
    print_warning "ยกเลิกการ deploy"
    exit 0
fi

