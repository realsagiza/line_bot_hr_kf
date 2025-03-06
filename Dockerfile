# ใช้ Python 3.10 เป็น Base Image
FROM python:3.10

# ตั้งค่า Working Directory
WORKDIR /app

# คัดลอกไฟล์ requirements.txt และติดตั้ง dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกโค้ดทั้งหมดไปยัง container
COPY . .

# เปิดพอร์ต 5001
EXPOSE 5001

# รัน Flask App
CMD ["python", "app/main.py"]