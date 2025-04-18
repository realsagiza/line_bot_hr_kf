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
