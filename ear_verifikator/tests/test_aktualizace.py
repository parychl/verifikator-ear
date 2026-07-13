from ear_verifikator.gui.aktualizace import _jako_cisla, je_novejsi


def test_prevod_verze():
    assert _jako_cisla("v1.2.3") == (1, 2, 3)
    assert _jako_cisla("1.0.0") == (1, 0, 0)
    assert _jako_cisla("nesmysl") is None
    assert _jako_cisla("") is None


def test_porovnani_verzi():
    assert je_novejsi("v1.1.0", "1.0.0")
    assert je_novejsi("2.0.0", "1.9.9")
    assert je_novejsi("1.10.0", "1.9.0")  # číselně, ne abecedně
    assert not je_novejsi("1.0.0", "1.0.0")
    assert not je_novejsi("v0.9.0", "1.0.0")
    assert not je_novejsi("beta", "1.0.0")  # nečíselný tag nikdy neupozorní
