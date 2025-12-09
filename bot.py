import os
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv
import db


# ---------- ENV ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "fleet.db")


# ---------- FSM STATES ----------

class AddCarStates(StatesGroup):
    vin = State()
    mileage = State()
    year = State()
    owner_company = State()
    model = State()
    plate = State()
    fuel_type = State()


class NewServiceStates(StatesGroup):
    car_plate = State()
    choose_mechanic = State()
    description = State()
    desired_at = State()


class CompleteServiceStates(StatesGroup):
    svc_id = State()
    final_mileage = State()
    cost_net = State()
    comments = State()


class EditCarStates(StatesGroup):
    waiting_car_identifier = State()
    waiting_field_choice = State()
    waiting_new_value = State()
    waiting_confirm_delete = State()


class RejectServiceStates(StatesGroup):
    alt_time = State()


# ---------- HELPERS ----------

async def ensure_user_registered(message: Message):
    db.add_user(DB_PATH, message.from_user.id, message.from_user.full_name or "")
    db.promote_to_admin_if_first(DB_PATH, message.from_user.id)


async def check_admin(message: Message) -> bool:
    role = db.get_user_role(DB_PATH, message.from_user.id)
    return role == "admin"


def get_mechanics_from_db():
    """
    Zwraca listę mechaników z tabeli users: [{tg_id, full_name}, ...]
    """
    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT tg_id, full_name FROM users WHERE role = 'mechanic'")
    rows = cur.fetchall()
    conn.close()
    return rows


async def start_edit_car_flow(message: Message, state: FSMContext, identifier: str):
    """
    Wspólna funkcja do rozpoczęcia edycji auta po numerze / VIN / ID.
    """
    ident = identifier.strip().upper()

    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()
    car = None

    if ident.isdigit():
        cur.execute("SELECT * FROM cars WHERE id = ?", (int(ident),))
        car = cur.fetchone()

    if not car:
        cur.execute(
            "SELECT * FROM cars WHERE UPPER(plate) = UPPER(?) OR UPPER(vin) = UPPER(?)",
            (ident, ident),
        )
        car = cur.fetchone()

    conn.close()

    if not car:
        await message.answer("❗ Samochód nie został znaleziony. Sprawdź numer / VIN lub użyj /list_cars.")
        return

    await state.update_data(car_id=car["id"])
    await state.set_state(EditCarStates.waiting_field_choice)

    text = (
        f"Samochód ID: {car['id']}\n"
        f"Numer rejestracyjny: {car['plate'] or '-'}\n"
        f"VIN: {car['vin']}\n"
        f"Model: {car['model'] or '-'}\n"
        f"Firma właściciela: {car['owner_company'] or '-'}\n"
        f"Rok: {car['year']} | Przebieg: {car['mileage']} km\n\n"
        "Co chcesz zmienić?"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="VIN", callback_data="editcar:field:vin"),
                InlineKeyboardButton(text="Przebieg", callback_data="editcar:field:mileage"),
                InlineKeyboardButton(text="Rok", callback_data="editcar:field:year"),
            ],
            [
                InlineKeyboardButton(text="Firma", callback_data="editcar:field:owner_company"),
                InlineKeyboardButton(text="Model", callback_data="editcar:field:model"),
            ],
            [
                InlineKeyboardButton(text="Numer rejestracyjny", callback_data="editcar:field:plate"),
                InlineKeyboardButton(text="Paliwo", callback_data="editcar:field:fuel_type"),
            ],
            [
                InlineKeyboardButton(text="🗑 Usuń samochód", callback_data="editcar:delete"),
            ],
        ]
    )

    await message.answer(text, reply_markup=kb)


# ---------- BOT SETUP ----------

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ======================================================================
#                         KOMENDY PODSTAWOWE
# ======================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await ensure_user_registered(message)
    role = db.get_user_role(DB_PATH, message.from_user.id) or "user"

    text = (
        f"Witaj, {message.from_user.full_name}!\n"
        f"Twoja rola: <b>{role}</b>\n\n"
        "Dostępne komendy:\n"
        "/whoami — pokaż swoją rolę\n"
        "/add_mechanic <id> — nadaj rolę mechanika\n"
        "/add_car — dodaj samochód\n"
        "/list_cars — lista samochodów\n"
        "/service_new — nowe zgłoszenie serwisowe\n"
        "/edit_car — edycja samochodu\n"
        "/report_month YYYY-MM — raport miesięczny\n"
    )
    await message.answer(text)


@dp.message(Command("whoami"))
async def cmd_whoami(message: Message):
    await ensure_user_registered(message)
    role = db.get_user_role(DB_PATH, message.from_user.id)
    await message.answer(
        f"Twój Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Rola: <b>{role}</b>"
    )


@dp.message(Command("add_mechanic"))
async def cmd_add_mechanic(message: Message):
    await ensure_user_registered(message)

    if not await check_admin(message):
        await message.answer("❌ Nie masz uprawnień administratora.")
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Użycie: /add_mechanic <telegram_id>")
        return

    try:
        tg_id = int(parts[1])
    except ValueError:
        await message.answer("Telegram ID musi być liczbą.")
        return

    ok = db.set_user_role(DB_PATH, tg_id, "mechanic")
    if ok:
        await message.answer(f"Użytkownik {tg_id} został ustawiony jako mechanik.")
        try:
            await bot.send_message(tg_id, "Otrzymałeś rolę mechanika w systemie floty.")
        except Exception:
            pass
    else:
        await message.answer("Nie znaleziono użytkownika o podanym ID. Musi najpierw napisać do bota /start.")


# ======================================================================
#                             DODAWANIE SAMOCHODU
# ======================================================================

@dp.message(Command("add_car"))
async def cmd_add_car(message: Message, state: FSMContext):
    await ensure_user_registered(message)

    if not await check_admin(message):
        await message.answer("❌ Brak uprawnień administratora.")
        return

    await state.set_state(AddCarStates.vin)
    await message.answer("Wprowadź VIN samochodu:")


@dp.message(AddCarStates.vin)
async def add_car_vin(message: Message, state: FSMContext):
    vin = message.text.strip().upper()

    if len(vin) < 5:
        await message.answer("VIN jest zbyt krótki. Wprowadź ponownie:")
        return

    if db.get_car_by_vin(DB_PATH, vin):
        await message.answer("Samochód z takim VIN już istnieje w systemie.")
        return

    await state.update_data(vin=vin)
    await state.set_state(AddCarStates.mileage)
    await message.answer("Wprowadź przebieg (km):")


@dp.message(AddCarStates.mileage)
async def add_car_mileage(message: Message, state: FSMContext):
    try:
        mileage = int(message.text.strip())
        if mileage < 0:
            raise ValueError
    except Exception:
        await message.answer("Przebieg musi być liczbą dodatnią. Wprowadź ponownie:")
        return

    await state.update_data(mileage=mileage)
    await state.set_state(AddCarStates.year)
    await message.answer("Rok produkcji (np. 2018):")


@dp.message(AddCarStates.year)
async def add_car_year(message: Message, state: FSMContext):
    try:
        year = int(message.text.strip())
        if year < 1980 or year > datetime.now().year + 1:
            raise ValueError
    except Exception:
        await message.answer("Niepoprawny rok. Wprowadź jeszcze raz:")
        return

    await state.update_data(year=year)
    await state.set_state(AddCarStates.owner_company)
    await message.answer("Podaj firmę właściciela (dla faktur):")


@dp.message(AddCarStates.owner_company)
async def add_car_owner(message: Message, state: FSMContext):
    await state.update_data(owner_company=message.text.strip())
    await state.set_state(AddCarStates.model)
    await message.answer("Model samochodu (lub '-' jeśli pomijamy):")


@dp.message(AddCarStates.model)
async def add_car_model(message: Message, state: FSMContext):
    model = message.text.strip()
    if model == "-":
        model = None
    await state.update_data(model=model)
    await state.set_state(AddCarStates.plate)
    await message.answer("Numer rejestracyjny (np. WE649LT):")


@dp.message(AddCarStates.plate)
async def add_car_plate(message: Message, state: FSMContext):
    plate = message.text.strip().upper()
    await state.update_data(plate=plate)
    await state.set_state(AddCarStates.fuel_type)
    await message.answer("Typ paliwa (benzyna/diesel/gaz/elektryczne):")


@dp.message(AddCarStates.fuel_type)
async def add_car_fuel(message: Message, state: FSMContext):
    fuel_type = message.text.strip()
    data = await state.get_data()

    car_id = db.add_car(
        DB_PATH,
        vin=data["vin"],
        mileage=data["mileage"],
        year=data["year"],
        owner_company=data["owner_company"],
        model=data.get("model"),
        plate=data.get("plate"),
        fuel_type=fuel_type,
    )

    await state.clear()

    await message.answer(
        f"Samochód został dodany.\n"
        f"ID: {car_id}\n"
        f"Numer: {data['plate']}\n"
        f"VIN: {data['vin']}\n"
        f"Firma: {data['owner_company']}\n"
        f"Przebieg: {data['mileage']} km"
    )


# ======================================================================
#                              LISTA AUT
# ======================================================================

@dp.message(Command("list_cars"))
async def cmd_list_cars(message: Message):
    await ensure_user_registered(message)
    cars = db.list_cars(DB_PATH, limit=50)

    if not cars:
        await message.answer("Brak samochodów w systemie.")
        return

    lines = ["Lista samochodów:\n"]
    for c in cars:
        lines.append(
            f"ID: {c['id']}\n"
            f"Numer: {c['plate'] or '-'}\n"
            f"VIN: {c['vin']}\n"
            f"Model: {c['model'] or '-'}\n"
            f"Firma: {c['owner_company'] or '-'}\n"
            f"Rok: {c['year']} | Przebieg: {c['mileage']} km\n"
            "---------------------------"
        )

    await message.answer("\n".join(lines))


# ======================================================================
#                          EDYCJA AUTA: /edit_car
# ======================================================================

@dp.message(Command("edit_car"))
async def cmd_edit_car(message: Message, state: FSMContext):
    await ensure_user_registered(message)

    if not await check_admin(message):
        await message.answer("❌ Brak uprawnień administratora.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        identifier = parts[1]
        await start_edit_car_flow(message, state, identifier)
    else:
        await state.set_state(EditCarStates.waiting_car_identifier)
        await message.answer("Wprowadź numer rejestracyjny, VIN lub ID auta do edycji:")


@dp.message(EditCarStates.waiting_car_identifier)
async def edit_car_choose_identifier(message: Message, state: FSMContext):
    identifier = message.text.strip()
    await start_edit_car_flow(message, state, identifier)


@dp.callback_query(F.data.startswith("editcar:field:"))
async def callback_edit_car_field(call: CallbackQuery, state: FSMContext):
    role = db.get_user_role(DB_PATH, call.from_user.id)
    if role != "admin":
        await call.answer("Brak uprawnień.", show_alert=True)
        return

    _, _, field = call.data.split(":")
    data = await state.get_data()
    if "car_id" not in data:
        await call.answer("Sesja edycji utracona.", show_alert=True)
        return

    await state.update_data(field=field)
    await state.set_state(EditCarStates.waiting_new_value)
    await call.answer()

    field_prompts = {
        "vin": "Wprowadź nowy VIN:",
        "mileage": "Wprowadź nowy przebieg (km):",
        "year": "Wprowadź nowy rok produkcji:",
        "owner_company": "Wprowadź nazwę firmy właściciela:",
        "model": "Wprowadź nowy model:",
        "plate": "Wprowadź nowy numer rejestracyjny:",
        "fuel_type": "Wprowadź typ paliwa:",
    }

    prompt = field_prompts.get(field, "Wprowadź nową wartość:")
    await call.message.answer(prompt)


@dp.message(EditCarStates.waiting_new_value)
async def edit_car_set_value(message: Message, state: FSMContext):
    data = await state.get_data()
    car_id = data.get("car_id")
    field = data.get("field")

    if not car_id or not field:
        await message.answer("Sesja edycji utracona. Spróbuj ponownie /edit_car.")
        await state.clear()
        return

    value = message.text.strip()

    if field == "mileage":
        try:
            value = int(value)
        except Exception:
            await message.answer("Przebieg musi być liczbą. Wprowadź ponownie:")
            return
    elif field == "year":
        try:
            value = int(value)
        except Exception:
            await message.answer("Rok musi być liczbą. Wprowadź ponownie:")
            return
    elif field in ("vin", "plate"):
        value = value.upper()

    allowed_fields = {"vin", "mileage", "year", "owner_company", "model", "plate", "fuel_type"}
    if field not in allowed_fields:
        await message.answer("Tego pola nie można zmienić.")
        await state.clear()
        return

    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"UPDATE cars SET {field} = ? WHERE id = ?", (value, car_id))
    conn.commit()
    conn.close()

    await state.clear()

    car = db.get_car_by_id(DB_PATH, car_id)
    await message.answer(
        "Dane samochodu zostały zaktualizowane:\n"
        f"ID: {car['id']}\n"
        f"Numer: {car['plate'] or '-'}\n"
        f"VIN: {car['vin']}\n"
        f"Model: {car['model'] or '-'}\n"
        f"Firma: {car['owner_company'] or '-'}\n"
        f"Rok: {car['year']} | Przebieg: {car['mileage']} km"
    )


@dp.callback_query(F.data == "editcar:delete")
async def callback_edit_car_delete(call: CallbackQuery, state: FSMContext):
    role = db.get_user_role(DB_PATH, call.from_user.id)
    if role != "admin":
        await call.answer("Brak uprawnień.", show_alert=True)
        return

    data = await state.get_data()
    if "car_id" not in data:
        await call.answer("Sesja utracona.", show_alert=True)
        return

    await state.set_state(EditCarStates.waiting_confirm_delete)
    await call.answer()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tak, usuń", callback_data="editcar:delete:yes"),
                InlineKeyboardButton(text="❌ Nie", callback_data="editcar:delete:no"),
            ]
        ]
    )
    await call.message.answer("Czy na pewno chcesz usunąć ten samochód?", reply_markup=kb)


@dp.callback_query(F.data == "editcar:delete:yes")
async def callback_edit_car_delete_yes(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    car_id = data.get("car_id")

    if not car_id:
        await call.answer("Sesja utracona.", show_alert=True)
        return

    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM cars WHERE id = ?", (car_id,))
    conn.commit()
    conn.close()

    await state.clear()
    await call.answer("Usunięto.")
    await call.message.answer(f"Samochód ID {car_id} został usunięty.")


@dp.callback_query(F.data == "editcar:delete:no")
async def callback_edit_car_delete_no(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Anulowano.")
    await call.message.answer("Usuwanie anulowane.")


# ======================================================================
#                         NOWE ZGŁOSZENIE SERWISOWE
# ======================================================================

@dp.message(Command("service_new"))
async def cmd_service_new(message: Message, state: FSMContext):
    await ensure_user_registered(message)

    if not await check_admin(message):
        await message.answer("❌ Brak uprawnień administratora.")
        return

    await state.set_state(NewServiceStates.car_plate)
    await message.answer("Wprowadź numer rejestracyjny samochodu (np. WE649LT):")


@dp.message(NewServiceStates.car_plate)
async def service_car_plate(message: Message, state: FSMContext):
    plate = message.text.strip().upper()

    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM cars WHERE UPPER(plate) = UPPER(?)", (plate,))
    car = cur.fetchone()
    conn.close()

    if not car:
        await message.answer("❗ Nie znaleziono samochodu o takim numerze. Wprowadź ponownie lub użyj /list_cars.")
        return

    await state.update_data(
        car_id=car["id"],
        plate=car["plate"],
        vin=car["vin"],
        owner_company=car["owner_company"],
    )

    mechs = get_mechanics_from_db()
    if not mechs:
        await message.answer("❗ W systemie nie ma żadnych mechaników. Dodaj ich przez /add_mechanic <id>.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(m["full_name"] or str(m["tg_id"])),
                    callback_data=f"choose_mech:{m['tg_id']}"
                )
            ]
            for m in mechs
        ]
    )

    await state.set_state(NewServiceStates.choose_mechanic)
    await message.answer("Wybierz mechanika:", reply_markup=kb)


@dp.callback_query(F.data.startswith("choose_mech:"))
async def callback_choose_mechanic(call: CallbackQuery, state: FSMContext):
    mech_id = int(call.data.split(":")[1])

    await state.update_data(mechanic_tg_id=mech_id)
    await state.set_state(NewServiceStates.description)

    await call.answer("Wybrano mechanika.")
    await call.message.delete()

    await bot.send_message(
        call.from_user.id,
        "Opisz problem / zakres prac serwisowych:"
    )


@dp.message(NewServiceStates.description)
async def service_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(NewServiceStates.desired_at)
    await message.answer("Wprowadź preferowaną datę i godzinę (np. 2025-12-05 11:00):")


@dp.message(NewServiceStates.desired_at)
async def service_desired_at(message: Message, state: FSMContext):
    desired = message.text.strip()
    data = await state.get_data()
    await state.clear()

    svc_id = db.create_service(
        DB_PATH,
        car_id=data["car_id"],
        mechanic_tg_id=data["mechanic_tg_id"],
        admin_tg_id=message.from_user.id,
        description=data["description"],
        desired_at=desired,
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Potwierdź", callback_data=f"svc_confirm:{svc_id}"),
                InlineKeyboardButton(text="❌ Odrzuć", callback_data=f"svc_reject:{svc_id}"),
            ]
        ]
    )

    text_mech = (
        f"Nowe zgłoszenie #{svc_id}\n"
        f"Samochód: {data['plate']}\n"
        f"VIN: {data['vin']}\n"
        f"Firma właściciela: {data['owner_company'] or '-'}\n"
        f"Opis: {data['description']}\n"
        f"Data/godzina: {desired}\n\n"
        "Potwierdź lub odrzuć:"
    )

    try:
        await bot.send_message(data["mechanic_tg_id"], text_mech, reply_markup=kb)
        await message.answer(f"Zgłoszenie serwisowe #{svc_id} zostało utworzone i wysłane do mechanika.")
    except Exception as e:
        await message.answer(f"⚠️ Nie udało się wysłać zgłoszenia do mechanika.\nBłąd: {e}")


# ======================================================================
#                  CALLBACK: POTWIERDZENIE / ODRZUCENIE
# ======================================================================

@dp.callback_query(F.data.startswith("svc_confirm:"))
async def callback_confirm_service(call: CallbackQuery):
    svc_id = int(call.data.split(":")[1])
    svc = db.get_service(DB_PATH, svc_id)

    if not svc:
        await call.answer("Zgłoszenie nie zostało znalezione.", show_alert=True)
        return

    if svc["mechanic_tg_id"] != call.from_user.id:
        await call.answer("To nie jest twoje zgłoszenie.", show_alert=True)
        return

    if svc["status"] != "pending":
        await call.answer("Status został już zmieniony.", show_alert=True)
        return

    db.update_service_status(DB_PATH, svc_id, "confirmed")
    await call.answer("Zgłoszenie potwierdzone.")
    await call.message.edit_reply_markup(reply_markup=None)

    try:
        await bot.send_message(
            svc["admin_tg_id"],
            f"Mechanik potwierdził zgłoszenie serwisowe #{svc_id}."
        )
    except Exception:
        pass

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Zakończ serwis",
                    callback_data=f"svc_complete:{svc_id}"
                )
            ]
        ]
    )

    await call.message.answer(
        f"Po wykonaniu prac naciśnij 'Zakończ serwis' dla zgłoszenia #{svc_id}.",
        reply_markup=kb
    )


@dp.callback_query(F.data.startswith("svc_reject:"))
async def callback_reject_service(call: CallbackQuery, state: FSMContext):
    svc_id = int(call.data.split(":")[1])
    svc = db.get_service(DB_PATH, svc_id)

    if not svc:
        await call.answer("Zgłoszenie nie zostało znalezione.", show_alert=True)
        return

    if svc["mechanic_tg_id"] != call.from_user.id:
        await call.answer("To nie jest twoje zgłoszenie.", show_alert=True)
        return

    if svc["status"] not in ("pending", "confirmed"):
        await call.answer("Status został już zmieniony.", show_alert=True)
        return

    await state.set_state(RejectServiceStates.alt_time)
    await state.update_data(svc_id=svc_id)

    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"Odrzucasz zgłoszenie #{svc_id}.\n"
        f"Podaj swoją dostępną datę/godzinę (lub '-' jeśli całkowita odmowa):"
    )


@dp.message(RejectServiceStates.alt_time)
async def reject_alt_time(message: Message, state: FSMContext):
    data = await state.get_data()
    svc_id = data.get("svc_id")
    alt = message.text.strip()
    await state.clear()

    if not svc_id:
        await message.answer("Sesja utracona. Spróbuj ponownie.")
        return

    db.update_service_status(DB_PATH, svc_id, "rejected")
    svc = db.get_service(DB_PATH, svc_id)

    alt_text = alt if alt != "-" else "—"

    text_admin = (
        f"Mechanik ODRZUCIŁ zgłoszenie #{svc_id}.\n\n"
        f"Samochód: {svc['plate']}\n"
        f"VIN: {svc['vin']}\n"
        f"Firma: {svc['owner_company'] or '-'}\n"
        f"Pierwotna data: {svc['desired_at']}\n"
        f"Proponowany termin od mechanika: {alt_text}"
    )

    try:
        await bot.send_message(svc["admin_tg_id"], text_admin)
    except Exception:
        pass

    await message.answer("Dziękujemy. Twoja propozycja czasu została wysłana administratorowi.")


# ======================================================================
#                      ZAKOŃCZENIE SERWISU
# ======================================================================

@dp.callback_query(F.data.startswith("svc_complete:"))
async def callback_complete_service(call: CallbackQuery, state: FSMContext):
    svc_id = int(call.data.split(":")[1])
    svc = db.get_service(DB_PATH, svc_id)

    if not svc:
        await call.answer("Zgłoszenie nie zostało znalezione.", show_alert=True)
        return

    if svc["mechanic_tg_id"] != call.from_user.id:
        await call.answer("To nie jest twoje zgłoszenie.", show_alert=True)
        return

    if svc["status"] not in ("pending", "confirmed"):
        await call.answer("Nie można zakończyć tego zgłoszenia.", show_alert=True)
        return

    await state.set_state(CompleteServiceStates.final_mileage)
    await state.update_data(svc_id=svc_id)
    await call.answer()
    await call.message.answer(
        f"Zakończenie serwisu #{svc_id}.\n"
        f"Wprowadź aktualny przebieg (km):"
    )


@dp.message(CompleteServiceStates.final_mileage)
async def complete_final_mileage(message: Message, state: FSMContext):
    try:
        mileage = int(message.text.strip())
        if mileage < 0:
            raise ValueError
    except ValueError:
        await message.answer("Przebieg musi być liczbą dodatnią. Wprowadź jeszcze raz:")
        return

    await state.update_data(final_mileage=mileage)
    await state.set_state(CompleteServiceStates.cost_net)
    await message.answer("Wprowadź koszt netto (liczba, np. 500):")


@dp.message(CompleteServiceStates.cost_net)
async def complete_cost_net(message: Message, state: FSMContext):
    try:
        cost_net = float(message.text.replace(",", ".").strip())
        if cost_net < 0:
            raise ValueError
    except ValueError:
        await message.answer("Koszt musi być liczbą dodatnią. Wprowadź ponownie:")
        return

    await state.update_data(cost_net=cost_net)
    await state.set_state(CompleteServiceStates.comments)
    await message.answer("Dodaj komentarz/zalecenia (lub '-' jeśli brak):")


@dp.message(CompleteServiceStates.comments)
async def complete_comments(message: Message, state: FSMContext):
    comments = message.text.strip()
    if comments == "-":
        comments = None

    data = await state.get_data()
    await state.clear()

    db.set_service_result(
        DB_PATH,
        svc_id=data["svc_id"],
        final_mileage=data["final_mileage"],
        cost_net=data["cost_net"],
        comments=comments,
    )

    sum_vat = round(data["cost_net"] * 0.23, 2)
    sum_gross = round(data["cost_net"] + sum_vat, 2)

    await message.answer(
        f"Serwis #{data['svc_id']} zakończony.\n"
        f"Przebieg: {data['final_mileage']} km\n"
        f"NETTO: {data['cost_net']:.2f}\n"
        f"VAT 23%: {sum_vat:.2f}\n"
        f"BRUTTO: {sum_gross:.2f}"
    )

    svc = db.get_service(DB_PATH, data["svc_id"])
    admin_text = (
        f"ZGŁOSZENIE SERWISOWE ZAKOŃCZONE #{data['svc_id']}\n\n"
        f"Samochód: {svc['plate']}\n"
        f"VIN: {svc['vin']}\n"
        f"Firma: {svc['owner_company'] or '-'}\n"
        f"Końcowy przebieg: {data['final_mileage']} km\n"
        f"NETTO: {data['cost_net']:.2f}\n"
        f"VAT 23%: {sum_vat:.2f}\n"
        f"BRUTTO: {sum_gross:.2f}\n"
        f"Komentarz mechanika: {comments or '—'}"
    )

    try:
        await bot.send_message(svc["admin_tg_id"], admin_text)
    except Exception:
        pass


# ======================================================================
#                             RAPORT MIESIĘCZNY
# ======================================================================

@dp.message(Command("report_month"))
async def cmd_report_month(message: Message):
    await ensure_user_registered(message)

    if not await check_admin(message):
        await message.answer("❌ Brak uprawnień.")
        return

    parts = message.text.split()
    if len(parts) == 2:
        try:
            year_str, month_str = parts[1].split("-")
            year = int(year_str)
            month = int(month_str)
        except Exception:
            await message.answer("Użycie: /report_month YYYY-MM, np. /report_month 2025-12")
            return
    else:
        now = datetime.now()
        year, month = now.year, now.month

    sum_net, commission = db.monthly_report(DB_PATH, year, month)

    await message.answer(
        f"Raport za {year}-{month:02d}:\n"
        f"Suma NETTO zakończonych serwisów: <b>{sum_net:.2f}</b>\n"
        f"Prowizja 10%: <b>{commission:.2f}</b>"
    )


# ======================================================================
#                             STARTUP
# ======================================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN nie został ustawiony w .env")

    db.init_db(DB_PATH)
    print("Baza danych zainicjalizowana.")
    print("Bot uruchomiony.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
