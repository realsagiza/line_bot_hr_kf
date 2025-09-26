# LINE Bot HR KF

## รายละเอียดโปรแกรม
โปรแกรมสำหรับการจัดการคำขอเบิกเงินผ่าน LINE Bot โดยมีระบบการอนุมัติและปฏิเสธคำขอ

## ฟีเจอร์หลัก
1. รับคำขอเบิกเงินผ่าน LINE Bot
2. หน้าเว็บแอปพลิเคชันสำหรับการอนุมัติคำขอ
3. เชื่อมต่อกับ API ของตู้เบิกเงินอัตโนมัติ (ทั้งสาขาโนนิโกะและคลังห้องเย็น)
4. บันทึกข้อมูลธุรกรรมลงในฐานข้อมูล MongoDB

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

## อัปเดตล่าสุด
- เพิ่มการบันทึกข้อมูลธุรกรรมใน collection `transactions` ในฐานข้อมูล MongoDB หลังจากอนุมัติคำขอเบิกเงินสำเร็จ
- ปรับปรุงการเชื่อมต่อฐานข้อมูล MongoDB ให้ใช้ค่าจาก environment variable

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
