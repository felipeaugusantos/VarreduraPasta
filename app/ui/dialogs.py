from tkinter import messagebox


def mostrar_erro(parent, titulo: str, mensagem: str) -> None:
    messagebox.showerror(titulo, mensagem, parent=parent)


def confirmar(parent, titulo: str, mensagem: str) -> bool:
    return messagebox.askyesno(titulo, mensagem, parent=parent)
