# LINE Bot HR KF

## รายละเอียดโปรแกรม
โปรแกรมสำหรับการจัดการคำขอเบิก/ฝากเงินผ่าน LINE Bot โดยมีระบบการอนุมัติและปฏิเสธคำขอ พร้อมบันทึกสถานะและวันที่ตามเวลาไทย (Bangkok +7) เพื่อใช้ทำรายงานได้ง่าย

## ฟีเจอร์หลัก
1. รับคำขอเบิกเงินผ่าน LINE Bot (ใช้ปุ่ม/เมนูเป็นหลัก แทนการพิมพ์ข้อความ)
2. หน้าเว็บแอปพลิเคชัน (LIFF) สำหรับการอนุมัติคำขอ
3. เชื่อมต่อกับ API ของตู้เบิก/ฝากเงินอัตโนมัติ (ทั้งสาขาโนนิโกะและคลังห้องเย็น)
4. บันทึกคำขอและข้อมูลธุรกรรมลงในฐานข้อมูล MongoDB แยกวันที่ตาม timezone Bangkok (+7)
5. เก็บประวัติการเปลี่ยนสถานะ (status history) ของคำขอ เพื่อใช้ตรวจสอบย้อนหลัง/ทำรายงาน

## การติดตั้ง
1. สร้างไฟล์ .env ด้วยค่าที่เหมาะสม:
   ```
   FLASK_APP=app.main
   FLASK_ENV=development
   LINE_CHANNEL_ACCESS_TOKEN=your_line_token
   LINE_CHANNEL_SECRET=your_line_secret
   WEBHOOK_URL=your_webhook_url
   MONGODB_URI=your_mongodb_connection_string
   ```

2. ติดตั้ง dependencies:
   ```
   pip install -r requirements.txt
   ```

3. รันแอปพลิเคชัน:
   ```
   python -m app.main
   ```

## โครงสร้างระบบ (High-level Spec)

### 1. Flow ฝั่งผู้ใช้ (พนักงาน)
- เริ่มจาก **Rich Menu** หรือปุ่มในห้องแชท (เช่น ปุ่ม "เบิกเงินสด", "ฝากเงินสด")
- ระบบจะให้เลือกข้อมูลผ่านปุ่ม/เมนูเป็นหลัก:
  - เลือกประเภทการทำรายการ (เบิก / ฝาก)
  - เลือกจำนวนเงินจากปุ่ม หรือไปที่ LIFF form หากต้องกรอกจำนวนเงิน/เหตุผลละเอียด
  - เลือกเหตุผล และสถานที่รับ/ฝากเงิน (คลังห้องเย็น / โนนิโกะ)
- เมื่อข้อมูลครบ:
  - ระบบจะสร้าง **หมายเลขคำขอ (`request_id`)** และบันทึกลง MongoDB (collection `withdraw_requests`)
  - บันทึกเวลา/วันที่ตาม **Bangkok timezone** และเก็บ `status_history`
  - ส่งข้อความสรุปกลับไปยังผู้ใช้ใน LINE ว่าคำขอถูกบันทึกและรออนุมัติ

### 2. Flow ฝั่งผู้อนุมัติ (หัวหน้า / ผู้มีสิทธิ์)
- เข้าหน้า LIFF `/money/approved-requests` (มีการตรวจสอบ userId ที่อนุญาตในหน้าเว็บ)
- เห็นรายการที่ `status = "pending"` สามารถเลือก:
  - ✅ อนุมัติ → ยิง API ไปยังตู้เบิกเงิน → ถ้าเครื่องตอบ `transaction_status = "success"` เท่านั้น จึง:
    - อัปเดต `withdraw_requests.status = "approved"`
    - อัปเดต `updated_at_*` และ `status_history`
    - บันทึกธุรกรรมลง collection `transactions` พร้อมวันที่ตามเวลาไทย
  - ❌ ปฏิเสธ → อัปเดต `withdraw_requests.status = "rejected"` และบันทึกเวลา/ประวัติใน `status_history`
- สามารถดูหน้า `/money/request-status` เพื่อดูรายการที่อนุมัติ/ปฏิเสธแล้ว

### 3. โครงสร้างข้อมูลใน MongoDB (สำคัญต่อการทำรายงาน)

#### 3.1 Collection: `withdraw_requests`
- ตัวอย่างฟิลด์สำคัญ:
  - `request_id`: string (รหัสคำขอ, unique)
  - `user_id`: string (LINE user id ของผู้ขอ)
  - `amount`: string/number (จำนวนเงินที่ขอ)
  - `reason`: string (เหตุผล หรือโค้ดเหตุผล)
  - `license_plate`: string | null (เลขทะเบียนกรณีเติมน้ำมัน)
  - `location`: string (เช่น `"คลังห้องเย็น"` หรือ `"โนนิโกะ"`)
  - `status`: string (`"pending"`, `"approved"`, `"rejected"`)
  - `created_at_bkk`: string (ISO datetime, เวลาไทย)
  - `created_at_utc`: string (ISO datetime, UTC)
  - `created_date_bkk`: string (`YYYY-MM-DD` ใช้ group รายงานรายวัน)
  - `updated_at_bkk`: string | null (เวลาที่เปลี่ยนสถานะล่าสุด, เวลาไทย)
  - `updated_at_utc`: string | null (เวลาที่เปลี่ยนสถานะล่าสุด, UTC)
  - `status_history`: array ของ object:
    - `{ status, at_bkk, at_utc, date_bkk, by }`

> หมายเหตุ: เวลาทั้งหมดที่เกี่ยวกับการรายงานจะอิงจาก **Bangkok timezone (+7)** เสมอ ผ่าน utility `time_utils.py`

#### 3.2 Collection: `transactions`
- เก็บธุรกรรมที่เกิดขึ้นจริงหลังจากอนุมัติคำขอแล้ว (ฝั่งเบิกเงิน)
- ฟิลด์สำคัญ:
  - `name`: string (ชื่อรายการ, ตอนนี้ใช้ค่าจาก `reason`)
  - `amount`: number (จำนวนเงิน)
  - `type`: string (เช่น `"expense"`)
  - `selectedStorage`: string (สถานที่ที่เกี่ยวข้อง เช่น `"คลังห้องเย็น"` หรือ `"โนนิโกะ"`)
  - `selectedDate`: string (`YYYY-MM-DD` อ้างอิงเวลาไทย)
  - `transaction_at_bkk`: string (ISO datetime เวลาไทย)
  - `transaction_at_utc`: string (ISO datetime UTC)
  - `transaction_date_bkk`: string (`YYYY-MM-DD` จากเวลาไทย)
  - `request_id`: string (เชื่อมกับ `withdraw_requests.request_id`)

> โครงสร้างนี้ช่วยให้สามารถดึงข้อมูลรายวัน/รายเดือนตามวันที่ไทยได้ง่าย เช่น group by `transaction_date_bkk` หรือ `created_date_bkk`

## อัปเดตล่าสุด
- เพิ่มการบันทึกข้อมูลธุรกรรมใน collection `transactions` ในฐานข้อมูล MongoDB หลังจากอนุมัติคำขอเบิกเงินสำเร็จ
- ปรับปรุงการเชื่อมต่อฐานข้อมูล MongoDB ให้ใช้ค่าจาก environment variable
- เพิ่ม utility `time_utils.py` สำหรับดึงเวลาแบบ timezone Bangkok (+7) และ UTC
- ปรับให้:
  - การสร้างคำขอเบิกเงินบันทึก `created_at_bkk`, `created_at_utc`, `created_date_bkk` และ `status_history`
  - การอนุมัติ/ปฏิเสธคำขอจะอัปเดต `updated_at_*` และ `status_history` และบันทึกธุรกรรมพร้อมวันที่ตามเวลาไทย

## ข้อกำหนด API ภายนอก
- Endpoint ฝากเงินต้องส่ง Header ต่อไปนี้เสมอ:
  - `X-Trace-Id`: ไอดีติดตามรูปแบบ `t-<8 hex>`
  - `X-Request-Id`: ไอดีคำขอรูปแบบ `r-<8 hex>`
- ตัวอย่างคำสั่งทดสอบ:
```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -H 'X-Trace-Id: t-9' -H 'X-Request-Id: r-9' \
  -d '{"amount": 250, "machine_id":"M1", "branch_id":"B1"}' \
  http://localhost:5050/api/deposit | jq
```
