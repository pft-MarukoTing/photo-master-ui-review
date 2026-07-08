export default function ViewSampleRefFileInput(props) {
  const {
    React,
    helper,
    useSelector,
    multiple,
    sampleFiles,
    selectedSampleFileId,
    onChange,
  } = props;

  const { templateParamGroups, templateParams } = useSelector(
    (state) => state.templateReducer
  );

  const ce = React.createElement;

  const [hoveredIdx, setHoveredIdx] = React.useState(null);

  const isMTTemplate = function (tpl) {
    var info = (tpl && tpl.info) || {};
    var title = info.i18n && info.i18n[0] && info.i18n[0].data && info.i18n[0].data.title;
    return typeof title === 'string' && title.indexOf('MT_Template') === 0;
  };

  const templates = (Array.isArray(templateParams) ? templateParams.slice() : []).sort(
    function (a, b) {
      var aMT = isMTTemplate(a), bMT = isMTTemplate(b);
      if (aMT !== bMT) return aMT ? 1 : -1;
      return (a.createdTime || 0) - (b.createdTime || 0);
    }
  );

  const DUMMY_THUMB =
    'data:image/svg+xml;utf8,' +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">' +
        '<rect width="120" height="120" fill="#e5e7eb"/>' +
        '<text x="60" y="70" font-size="40" text-anchor="middle" fill="#9ca3af" font-family="sans-serif">?</text>' +
      '</svg>'
    );

  var infoOf = function (tpl) {
    return (tpl && tpl.info) || {};
  };
  var thumbUrlOf = function (tpl) {
    return infoOf(tpl).pImage || '';
  };
  var refUrlOf = function (tpl) {
    return infoOf(tpl).template_ref_image || '';
  };
  var idOf = function (tpl, idx) {
    if (tpl && tpl.id != null) return String(tpl.id);
    if (tpl && tpl.value != null) return String(tpl.value);
    return 'tpl_' + idx;
  };
  var labelOf = function (tpl, idx) {
    var info = infoOf(tpl);
    if (info.i18n && info.i18n[0] && info.i18n[0].data && info.i18n[0].data.title) {
      return info.i18n[0].data.title;
    }
    if (tpl && tpl.guid) return tpl.guid;
    if (tpl && tpl.label) return tpl.label;
    return 'template ' + (idx + 1);
  };

  var handleSelect = function (tpl, idx) {
    var refUrl = refUrlOf(tpl);
    if (!refUrl) {
      console.warn('[ViewSampleRefFileInput] no template_ref_image on', tpl);
      return;
    }
    var id = idOf(tpl, idx);
    var name = labelOf(tpl, idx) + '.jpg';
    onChange([
      {
        id: id,
        name: name,
        type: 'image/jpeg',
        size: 0,
        data: null,
        url: refUrl,
      },
    ]);
  };

  var cards = templates.map(function (tpl, idx) {
    var id = idOf(tpl, idx);
    var thumb = thumbUrlOf(tpl) || DUMMY_THUMB;
    var label = labelOf(tpl, idx);
    var selectedIds = Array.isArray(selectedSampleFileId)
      ? selectedSampleFileId.map(String)
      : (selectedSampleFileId != null ? [String(selectedSampleFileId)] : []);
    var isSelected = selectedIds.indexOf(id) !== -1;
    var isHovered = hoveredIdx === idx;

    return ce(
      'div',
      {
        key: id,
        onClick: function () { handleSelect(tpl, idx); },
        onMouseEnter: function () { setHoveredIdx(idx); },
        onMouseLeave: function () { setHoveredIdx(null); },
        style: {
          cursor: 'pointer',
          flex: '0 0 auto',
          width: 150,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        },
      },
      ce(
        'div',
        {
          style: {
            height: '2.6em',
            lineHeight: 1.3,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
          },
        },
        ce(
          'div',
          {
            title: label,
            style: {
              fontSize: 14,
              color: isSelected ? '#111827' : '#6b7280',
              fontWeight: isSelected ? 600 : 500,
              textAlign: 'center',
              overflow: 'hidden',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              wordBreak: 'break-word',
              lineHeight: 1.3,
              transition: 'color 0.15s',
            },
          },
          label
        )
      ),
      ce(
        'div',
        {
          style: {
            position: 'relative',
            width: '100%',
            aspectRatio: '4 / 5',
            borderRadius: 6,
            overflow: 'hidden',
            border: isSelected ? '4px solid #3b82f6' : '4px solid transparent',
            outline: isHovered && !isSelected ? '1px solid #d1d5db' : 'none',
            background: '#e5e7eb',
            transition: 'border-color 0.15s, outline-color 0.15s',
          },
        },
        ce('img', {
          src: thumb,
          alt: label,
          style: {
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            display: 'block',
          },
        })
      )
    );
  });

  return ce(
    'div',
    {
      style: {
        overflowX: 'auto',
        overflowY: 'hidden',
        padding: 8,
      },
    },
    templates.length > 0
      ? ce(
          'div',
          {
            style: {
              display: 'flex',
              flexDirection: 'row',
              flexWrap: 'nowrap',
              justifyContent: 'space-around',
              gap: 12,
              minWidth: '100%',
              width: 'fit-content',
            },
          },
          cards
        )
      : ce(
          'div',
          {
            style: { fontSize: 13, color: '#9ca3af', fontStyle: 'italic' },
          },
          'No templates available yet.'
        )
  );
}
