# Verilənlər bazası və yayım

Yeni quraşdırmada və yeni miqrasiya əlavə ediləndə yayım qabağı işlət:

```powershell
.\.venv\Scripts\python.exe -B migrate.py
```

`DATABASE_URL` mühitdən və ya yerli `.env` faylından oxunur. Miqrasiyalar
bir tranzaksiyada, kilid altında tətbiq edilir. `schema_migrations` tətbiq
edilmiş faylların adını və yoxlama cəmini saxlayır; təkrar işə salmaq təhlükəsizdir.
Tətbiq edilmiş SQL faylını dəyişmə, növbəti nömrəli fayl əlavə et.
Bot açılarkən miqrasiya avtomatik işləmir.

`001_schema.sql` mövcud əsas cədvəlləri saxlayır, çatışmayanları yaradır.
`002_shopping_and_delivery.sql` alış-veriş cədvəlini və çatdırılma statusunu
əlavə edir. Mövcud ərzaq və resept sətirləri silinmir. Köhnə çatdırılma
qeydləri tamamlanmış sayılır. Köhnə seçilmiş reseptlər yeni porsiya və növ
variantları ilə yanaşı oxunur; məcburi yenidən yazılmır.

## Çatdırılma və sessiya

QStash `ne-bishirim` növbəsinin parallelism dəyərini **1** saxla. Onu artırmaq
üçün əvvəlcə istifadəçi üzrə sessiya kilidlənməsi əlavə edilməlidir.
Worker başlamadan yeniləmə ID-si atomik tutulur. Sessiya ilə tamamlanma
qeydi eyni DB tranzaksiyasında yazılır. Handler başlamazdan əvvəlki xətada
təkrar cəhdə icazə verilir. Handler başladıqdan sonra nəticə qeyri-müəyyəndirsə
avtomatik təkrar icra edilmir: Telegram mesajlarını DB ilə bir tranzaksiyada
geri qaytarmaq mümkün deyil. Bu seçim bəzi yarımçıq əməliyyatların menyudan
yenidən yoxlanmasını tələb edir, əvəzində eyni mesajların təkrar göndərilməsini azaldır.
5 dəqiqədən köhnə işlənən qeyd növbəti çatdırılmada `uncertain` olur.
Problemdə menyunu yenidən açıb nəticəni yoxla; lazım gəlsə `/start` yaz.

## İstifadəçi məlumatları

Ərzaqlar, seçilmişlər, alış-veriş siyahısı və sessiya istifadəçi ID-si ilə
saxlanır. `/delete_my_data` şəxsi söhbətdə 5 dəqiqəlik təsdiq tələb edir.
Təsdiqdən sonra bu məlumatlar birlikdə silinir, çatdırılma qeydlərində
istifadəçi ID-si boşaldılır. Anonim yeniləmə ID-si təkrar çatdırılmanı bloklayır.
Telegram söhbət tarixçəsi və xarici xidmət qeydləri bu əməliyyatla silinmir.

Alış-veriş bölməsi menyudan gizlədilib; əvvəlki siyahılar saxlanır və hesab
silinməsinə daxildir. Köhnə düymələr siyahını dəyişmir. Çatışmayan ərzaqlar
reseptdə görünür. Vaxt seçimləri Hamısı, ≤45 dəq, 46–90 dəq-dir; Hamısı
90 dəqiqədən uzun yeməkləri də əhatə edir. Köhnə 30 və 60 dəqiqə seçimləri
uyğun olaraq ≤45 və 46–90-a keçir. Hər yeni resept axtarışı Hamısı və 2 nəfər
ilə başlayır; həmin axtarış daxilində dəyişdirilən seçimlər saxlanır.
Filtrə basmaq mövcud siyahını süzür, AI çağırmır. Boş nəticədə ayrıca axtarış
düyməsi var. Yeni axtarışlara vaxt və porsiya ötürülür; başqa aralıqdakı
namizədlər itirilmir. Tam reseptin dəqiqləşən vaxtı yenidən yoxlanır.
Ad və duz/istiot dəyişiklikləri eyni üsul və əsas ərzaqlarla yeni təklif sayılmır.
Model təlimatları müxtəlif real yeməklər istəyir, keyfiyyətə zəmanət vermir.
1/2/4 nəfər seçimi tam resept hazırlanmasına ötürülür; vaxt və temperatur
sadəcə vurulmur. AI modeli bu dəyişikliklə əvəz edilmir.
