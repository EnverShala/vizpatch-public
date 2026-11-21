using System;

namespace VizpatchAddin.Core
{
    /// <summary>
    /// Die Hinweistexte unter den Urlaubsfeldern im Einstellungen-Dialog und die
    /// clientseitige Zeitraum-Pruefung (URL-01..09) — reine Logik ohne UI, damit
    /// beides testbar ist (Muster <see cref="UpdateWindowState"/>).
    ///
    /// Warum die Texte hier und nicht als Literale im Dialog liegen: sie tragen
    /// die einzige Warnung des Produkts vor einer Funktion, die selbstaendig
    /// Mails an Dritte verschickt. Ein Tippfehler oder ein versehentlich
    /// geloeschter Halbsatz waere im Dialog nicht bemerkbar; hier haengt ein
    /// Test daran.
    ///
    /// Die Pruefung in <see cref="ValidateZeitraum"/> ist eine VORPRUEFUNG, kein
    /// Ersatz fuer die serverseitige: das Backend prueft denselben Fall noch
    /// einmal (<c>_pruefe_urlaubszeitraum</c> in <c>webui/src/main.py</c>), weil
    /// ein Client nie die letzte Instanz sein darf. Sie existiert, damit der
    /// Betreiber den Fehler SOFORT sieht statt nach einem Roundtrip.
    /// </summary>
    public static class UrlaubHintText
    {
        /// <summary>
        /// Alles bereit: Vorlage vorhanden, Zeitraum pflegbar.
        ///
        /// Sagt bewusst NUR, wer KEINE Antwort bekommt. Dass im Zeitraum jeder
        /// Absender genau einmal die Vorlage „## Urlaub" erhaelt, steht schon im
        /// roten Warnblock oberhalb der Felder — bis zum 2026-08-24 stand es in
        /// beiden Texten fast wortgleich, und der Betreiber hat es gemeldet
        /// („die 2 Erklaerungen sind im Prinzip die selben"). Zwei Absaetze, die
        /// dasselbe sagen, liest man beim zweiten Mal nicht mehr — und dann geht
        /// die eine Information unter, die nur hier steht.
        /// </summary>
        public const string Ready =
            "Newsletter, Automaten und No-Reply-Adressen bekommen nie eine "
            + "Antwort — auch nicht Mails, in denen dein Postfach nur im "
            + "Kopie-Feld steht.";

        /// <summary>
        /// Es gibt keinen Abschnitt <c>## Urlaub</c> in den Vorlagen. Ohne ihn
        /// wird NICHTS gesendet — es wird bewusst kein Ersatztext erfunden.
        /// </summary>
        public const string MissingTemplate =
            "Achtung: Es gibt noch keine Vorlage „## Urlaub“. Ohne sie wird "
            + "NICHTS verschickt. Lege sie im Chat an („leg eine Vorlage Urlaub "
            + "an“) oder in den Vorlagen der Weboberfläche.";

        /// <summary>
        /// Prueft den eingegebenen Zeitraum. Rueckgabe: Fehlermeldung fuer den
        /// Betreiber, oder <c>null</c> wenn alles passt.
        ///
        /// Leer + leer ist gueltig, SOLANGE die Funktion nicht aktiviert wird —
        /// so laesst sich ein vergangener Urlaub austragen. Wird sie aktiviert,
        /// sind beide Daten Pflicht: eine aktive Urlaubsantwort ohne Zeitraum
        /// wuerde nie greifen, und der Betreiber saehe nur einen gesetzten Haken.
        ///
        /// Akzeptiert AUSSCHLIESSLICH <c>JJJJ-MM-TT</c>. Deutsche Schreibweise
        /// wird bewusst nicht geraten: bei <c>01.09.2026</c> gegen
        /// <c>09/01/2026</c> traefe jede Heuristik irgendwann den falschen Monat,
        /// und ein um Monate verschobener Urlaub verschickt entweder keine oder
        /// ganz falsche Autoantworten.
        /// </summary>
        public static string ValidateZeitraum(string von, string bis, bool aktiviert)
        {
            von = (von ?? "").Trim();
            bis = (bis ?? "").Trim();

            if (von.Length == 0 && bis.Length == 0)
            {
                return aktiviert
                    ? "Für die Urlaubs-Autoantwort müssen Von- und Bis-Datum gesetzt sein — "
                        + "ohne Zeitraum würde sie nie greifen."
                    : null;
            }

            if (von.Length == 0 || bis.Length == 0)
            {
                return "Bitte Von- UND Bis-Datum des Urlaubs angeben (Format JJJJ-MM-TT).";
            }

            DateTime vonDatum, bisDatum;
            if (!TryParseIso(von, out vonDatum) || !TryParseIso(bis, out bisDatum))
            {
                return "Urlaubsdatum bitte im Format JJJJ-MM-TT angeben (z. B. 2026-09-01).";
            }

            if (bisDatum < vonDatum)
            {
                return "Das Bis-Datum des Urlaubs liegt vor dem Von-Datum.";
            }

            return null;
        }

        /// <summary>
        /// <c>JJJJ-MM-TT</c> UND ein real existierender Tag. <c>ParseExact</c> mit
        /// <see cref="System.Globalization.CultureInfo.InvariantCulture"/>, damit
        /// die Windows-Regionseinstellung des Kunden das Ergebnis nicht
        /// verschiebt.
        /// </summary>
        public static bool TryParseIso(string wert, out DateTime datum)
        {
            return DateTime.TryParseExact(
                (wert ?? "").Trim(),
                "yyyy-MM-dd",
                System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.None,
                out datum);
        }
    }
}
