# NEXO Groups & Channels API

تمام درخواست‌های تغییردهنده‌ی وضعیت به نشست واردشده و هدر `X-CSRF` نیاز دارند.
شناسه‌ی `cid` شناسه‌ی گروه یا کانال است. مجوزها در سمت سرور بررسی می‌شوند.

## امکانات و مسیرها

- `GET /api/chats/<cid>/features` — تنظیمات و آمار خلاصه
- `PUT /api/chats/<cid>/features` — حالت فقط مدیران، تأیید عضویت، نمایش اعضا، ظرفیت، آهسته‌سازی، پیام خوشامد و قوانین
- `POST /api/chats/<cid>/invites` — لینک دعوت با محدودیت تعداد استفاده و انقضا
- `GET /api/chats/<cid>/invites` و `DELETE /api/chats/<cid>/invites/<iid>` — فهرست و لغو لینک‌ها
- `POST /api/invites/<token>/join` — پیوستن با لینک دعوت یا ثبت درخواست عضویت
- `GET /api/chats/<cid>/join-requests` و `POST /api/chats/<cid>/join-requests/<rid>` — بررسی درخواست‌ها با `action=approve|reject`
- `POST /api/chats/<cid>/polls`, `GET /api/chats/<cid>/polls` — ساخت و مشاهده نظرسنجی
- `POST /api/polls/<pid>/vote` — رأی با `options: [0]` یا چند گزینه در نظرسنجی چندانتخابی
- `POST /api/polls/<pid>/close` — بستن نظرسنجی
- `POST /api/chats/<cid>/topics`, `GET /api/chats/<cid>/topics` — موضوعات گروه
- `PATCH /api/topics/<tid>` — تغییر عنوان یا بستن موضوع
- `POST /api/chats/<cid>/moderation` — `kick`, `ban`, `mute`, `unmute`, `unban`, `promote`, `demote`
- `GET /api/chats/<cid>/events` — گزارش فعالیت مدیران
- `GET /api/chats/<cid>/stats` — آمار اعضا، پیام‌ها و فعالیت روزانه

### نمونه تنظیمات
```json
{
  "slow_mode": 10,
  "join_approval": 1,
  "members_visible": 1,
  "only_admins": 0,
  "max_members": 5000,
  "welcome": "به گروه خوش آمدید",
  "rules": "به یکدیگر احترام بگذارید"
}
```
`slow_mode` بر حسب ثانیه است. مدت سکوت یا مسدودسازی در endpoint مدیریت اعضا با `duration` بر حسب ثانیه تعیین می‌شود (۶۰ تا ۲۵۹۲۰۰۰).

## نکته‌ی انتشار
این افزونه API و منطق سمت سرور را اضافه می‌کند. برای نمایش کنترل‌های جدید در رابط کاربری چت، باید پنل مدیریت گروه/کانال در فرانت‌اند به این APIها متصل شود؛ این ZIP به‌تنهایی ادعای تکمیل آن رابط کاربری را ندارد. زمان‌بندی پست هم عمداً اضافه نشده، چون اجرای قابل‌اعتماد آن به worker پایدار در میزبان نیاز دارد.
