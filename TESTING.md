# Testlərin işlədilməsi

GitHub-da **Actions → Bot tests** bölməsi hər push və pull request üçün
asılılıqları, Python sintaksisini, tətbiqin qurulmasını və `tests/` daxilindəki
avtomatik sınaqları yoxlayır. **Run workflow** ilə əl ilə də başladılır.

Testlər Python 3.13 ilə işləyir. Telegram, Gemini və Neon üçün real açarlar
və GitHub Secrets lazım deyil. Tətbiqin qurulması addımında yalnız sınaq
dəyərləri istifadə olunur; bot başladılmır. Xarici xidmət əməliyyatları
testlərdə əvəzlənir və istifadəçi məlumatları dəyişdirilmir.

Windows-da layihə qovluğundan:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Digər mühitlərdə, asılılıqlar quraşdırıldıqdan sonra:

```sh
python -B -m unittest discover -s tests -v
python -m pip check
```

Hazırkı 15 test seçilmiş reseptlərin saxlanması, köhnə düymələrin bloklanması,
sessiyanın bərpası, saxlama xətası, menyu keçidləri, sahiblik parametrləri,
cari səbətə uyğun çatışmayan ərzaqlar və uzun mesajları əhatə edir.
Tam canlı Telegram/QStash axını və AI cavabının keyfiyyəti ayrıca yoxlanmalıdır.

Kök qovluqdakı `gemini_*test.py`, `gemini_compare.py` və
`nvidia_recipe_test.py` canlı API sınaqlarıdır; avtomatik testlərə daxil deyil.

Bu workflow test nəticəsini göstərir. Vercel-in avtomatik yayımlamasını
dayandırmır və branch protection qaydası yaratmır. Gələcək dəyişikliklərdə
pull request açıb yaşıl **Python tests** nəticəsini yoxlamaq məsləhətdir.
