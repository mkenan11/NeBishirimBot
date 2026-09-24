# Testlərin işlədilməsi

GitHub-da **Actions → Bot tests** hər push və pull request üçün asılılıqları,
Python sintaksisini, tətbiqin qurulmasını və avtomatik sınaqları yoxlayır.
**Run workflow** ilə əl ilə də başladılır. Python 3.13 istifadə olunur.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Digər mühitlərdə `.\.venv\Scripts\python.exe` əvəzinə `python` istifadə et.
67 şəbəkəsiz test seçilmişləri, menyu keçidlərini, resept yoxlamalarını,
porsiya və vaxt aralıqlarını, məqsədli axtarışı, təkrar təklifləri, ərzaq adlarını,
köhnə alış-veriş düymələrinin bloklanmasını,
3+1+1 və 3+2 bölgüsünü, üsul müxtəlifliyini, nömrə–düymə uyğunluğunu,
qısa axtarış həddini, sorğunun ləğvini, ilk xətadan sonra təkrar cəhdi,
çatışmayan qrupların tamamlanmasını və tam reseptdə ərzaq bölgüsünün qorunmasını,
silinmə təsdiqini və worker-in təkrar sorğulara davranışını yoxlayır.
Telegram və Gemini çağırışları əvəzlənir; real API açarları lazım deyil.

Əlavə 9 PostgreSQL sınağı üçün ayrıca test bazası göstər:

```powershell
$env:TEST_DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/ne_bishirim_test'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_database_integration.py -v
```

Bu dəyişən yoxdursa DB testləri buraxılır. Hər DB testi ayrıca sxem yaradır
və sonda bütün tranzaksiyanı geri qaytarır. CI müvəqqəti PostgreSQL 16
servisi işlədir; Neon açarı və GitHub Secrets tələb olunmur.
Sınaqlar miqrasiyanı, köhnə məlumatlarla uyğunluğu, istifadəçi sərhədlərini,
atomik sessiya yazılmasını və silinməni yoxlayır.

Tam canlı Telegram/QStash axını və AI resept keyfiyyəti ayrıca qiymətləndirilir.
Kök qovluqdakı `gemini_*test.py`, `gemini_compare.py` və
`nvidia_recipe_test.py` canlı API sınaqlarıdır; bu workflow-a daxil deyil.

Workflow Vercel-in avtomatik yayımını dayandırmır və branch protection
yaratmır. Yayım zamanı Actions nəticəsi və Vercel statusu ayrıca yoxlanmalıdır.
