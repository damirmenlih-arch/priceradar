import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import SKU, Subscription
from app.services.monitor import check_and_notify

logger = logging.getLogger(__name__)

router = Router(name="subscriptions")


class SubscribeStates(StatesGroup):
    waiting_for_sku = State()
    waiting_for_unsubscribe = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Я PriceRadar 📈\n\n"
        "Подпишись на товар, чтобы получать алерты, когда цена изменится "
        "или товар пропадёт из наличия.\n\n"
        "Команды:\n"
        "/subscribe — подписаться на SKU\n"
        "/unsubscribe — отписаться\n"
        "/subscriptions — мои подписки\n"
        "/check — внеплановая проверка цен прямо сейчас"
    )


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, state: FSMContext) -> None:
    await state.set_state(SubscribeStates.waiting_for_sku)
    await message.answer("Отправьте числовой SKU товара для подписки:")


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, state: FSMContext) -> None:
    await state.set_state(SubscribeStates.waiting_for_unsubscribe)
    await message.answer("Отправьте SKU, от которого хотите отписаться:")


@router.message(Command("cancel"), SubscribeStates.waiting_for_sku)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(Command("cancel"), SubscribeStates.waiting_for_unsubscribe)
async def cmd_cancel_unsub(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@router.message(Command("subscriptions"))
async def cmd_subscriptions(message: Message, session_factory: async_sessionmaker) -> None:
    user_id = message.from_user.id  # type: ignore[union-attr]
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(SKU.internal_sku, SKU.title)
                .join(Subscription, Subscription.sku_id == SKU.id)
                .where(Subscription.user_id == user_id, Subscription.is_active.is_(True))
                .order_by(SKU.internal_sku)
            )
        ).all()

    if not rows:
        await message.answer("У вас нет активных подписок. Оформите: /subscribe")
        return

    listing = "\n".join(f"• <code>{internal}</code> — {title}" for internal, title in rows)
    await message.answer(f"Ваши подписки ({len(rows)}):\n{listing}")


@router.message(Command("check"))
async def cmd_check(
    message: Message,
    session_factory: async_sessionmaker,
    bot,  # Bot — инжектится aiogram'ом; без аннотации, чтобы не перехватывать DI у других хендлеров
) -> None:
    await message.answer("🔄 Запускаю внеплановую проверку цен...")
    run, sent = await check_and_notify(session_factory, bot)
    failed = run.stats.get("failed_retries", 0) + run.stats.get("failed_parse", 0)
    await message.answer(
        "✅ Проверка завершена:\n"
        f"• всего SKU: {run.total}\n"
        f"• цен изменилось: {len(run.changed_sku_ids)}\n"
        f"• без изменений: {run.stats.get('unchanged', 0)}\n"
        f"• ошибок: {failed}\n"
        f"• уведомлений отправлено: {sent}"
    )


@router.message(SubscribeStates.waiting_for_sku, F.text.regexp(r"^\d{6,12}$"))
async def handle_sku_input(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
) -> None:
    internal_sku = message.text or ""
    user_id = message.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        async with session.begin():
            sku = await session.scalar(
                select(SKU).where(
                    SKU.internal_sku == internal_sku, SKU.is_active.is_(True)
                )
            )
            if sku is None:
                await message.answer(
                    f"❌ SKU <code>{internal_sku}</code> не найден среди отслеживаемых. Проверьте номер."
                )
                return

            exists = await session.scalar(
                select(Subscription.id).where(
                    Subscription.user_id == user_id,
                    Subscription.sku_id == sku.id,
                    Subscription.is_active.is_(True),
                )
            )
            if exists:
                await message.answer(
                    f"✅ Вы уже подписаны на <b>{sku.title}</b> (<code>{sku.internal_sku}</code>)."
                )
                await state.clear()
                return

            session.add(Subscription(user_id=user_id, sku_id=sku.id))
            sku_id, sku_title = sku.id, sku.title

    await state.clear()
    await message.answer(
        f"✅ Подписка оформлена: <b>{sku_title}</b> (<code>{internal_sku}</code>).\n"
        "Уведомления придут сюда при изменении цены.\n"
        "Проверить прямо сейчас: /check"
    )


@router.message(SubscribeStates.waiting_for_unsubscribe, F.text.regexp(r"^\d{6,12}$"))
async def handle_unsubscribe(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
) -> None:
    internal_sku = message.text or ""
    user_id = message.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        async with session.begin():
            sku = await session.scalar(select(SKU).where(SKU.internal_sku == internal_sku))
            if sku is None:
                await message.answer(f"❌ SKU <code>{internal_sku}</code> не найден.")
                return

            subs = (
                await session.scalars(
                    select(Subscription).where(
                        Subscription.user_id == user_id,
                        Subscription.sku_id == sku.id,
                        Subscription.is_active.is_(True),
                    )
                )
            ).all()
            if not subs:
                await message.answer("У вас нет активной подписки на этот SKU.")
                await state.clear()
                return

            for sub in subs:
                sub.is_active = False

    await state.clear()
    await message.answer(f"✅ Подписка на <code>{internal_sku}</code> отключена.")


@router.message(SubscribeStates.waiting_for_sku)
async def handle_sku_invalid(message: Message) -> None:
    await message.answer("SKU — число из 6–12 цифр. Попробуйте ещё раз или /cancel.")


@router.message(SubscribeStates.waiting_for_unsubscribe)
async def handle_unsubscribe_invalid(message: Message) -> None:
    await message.answer("SKU — число из 6–12 цифр. Попробуйте ещё раз или /cancel.")
