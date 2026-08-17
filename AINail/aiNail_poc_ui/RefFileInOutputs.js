export default function RefFileInOutputs(props) {
  const { React, useSelector, refFile } = props;

  const { templateParams } = useSelector((state) => state.templateReducer);

  const ce = React.createElement;

  var file = refFile || {};
  var list = Array.isArray(templateParams) ? templateParams : [];

  var infoOf = function (tpl) {
    return (tpl && tpl.info) || {};
  };

  // ViewSampleRefFileInput names each ref "<templateId>__<title>.jpg"; the host
  // rewrites the url into a blob: url, so the name is the only thing left that
  // ties this file back to a template, and titles alone are not unique.
  var name = String(file.name || '');
  var sep = name.indexOf('__');
  var templateId = sep === -1 ? '' : name.slice(0, sep);

  var match = null;
  if (templateId) {
    for (var i = 0; i < list.length; i++) {
      if (String(list[i] && list[i].id) === templateId) {
        match = list[i];
        break;
      }
    }
  }

  var src = (match && infoOf(match).pImage) || file.url || '';
  if (!src) return null;

  return ce('img', {
    src: src,
    alt: match ? '' : name,
    style: {
      display: 'block',
      maxWidth: '100%',
      height: 'auto',
      borderRadius: 4,
    },
  });
}
