from io import BytesIO

from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, PhotoSize

from app.api.client import api_client
from app.bot.keyboards import get_ocr_doc_type_keyboard
from app.bot.states import Ocr

router = Router()


@router.message(F.text == "Распознать документ")
async def handle_start_ocr(message: Message, state: FSMContext):
    await message.answer(
        "Выберите тип документа, который вы хотите распознать:",
        reply_markup=get_ocr_doc_type_keyboard(),
    )
    await state.set_state(Ocr.choosing_document_type)


@router.callback_query(Ocr.choosing_document_type, F.data.startswith("ocr_type:"))
async def handle_ocr_type_chosen(callback: CallbackQuery, state: FSMContext):
    doc_type = callback.data.split(":")[1]
    await state.update_data(doc_type=doc_type)

    await callback.message.edit_text(f"Вы выбрали: {doc_type}.")
    await callback.message.answer("Теперь, пожалуйста, отправьте мне фотографию документа.")
    await callback.answer()
    
    await state.set_state(Ocr.uploading_document)


@router.message(Ocr.uploading_document, F.photo)
async def handle_document_photo(message: Message, state: FSMContext, bot: Bot):
    await message.answer("Фото получено! Скачиваю и отправляю на сервер...")

    photo: PhotoSize = message.photo[-1]
    image_bytes = BytesIO()
    await bot.download(file=photo.file_id, destination=image_bytes)
    image_bytes.seek(0) # Возвращаем курсор в начало файла

    data = await state.get_data()
    doc_type = data.get("doc_type")

    result = await api_client.submit_document_for_extraction(
        doc_type=doc_type,
        image_bytes=image_bytes,
        filename=f"{photo.file_id}.jpg"
    )

    if result and result.get("task_id"):
        await message.answer(
            f"✅ Документ успешно отправлен в обработку!\n"
            f"<b>ID вашей задачи:</b> <code>{result['task_id']}</code>\n\n"
            f"Вы сможете проверить статус позже."
            # TODO: Добавить кнопку для проверки статуса
        )
    else:
        await message.answer("❌ Произошла ошибка при отправке документа. Попробуйте еще раз.")

    await state.clear() 